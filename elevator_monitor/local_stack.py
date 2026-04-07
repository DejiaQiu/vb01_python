from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _safe_token(value: str) -> str:
    token = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(value or ""))
    token = token.strip("_")
    return token or "unknown"


@dataclass(frozen=True)
class LocalStackPaths:
    run_dir: Path
    state_path: Path
    api_pid_path: Path
    monitor_pid_path: Path
    api_log_path: Path
    monitor_log_path: Path
    data_dir: Path
    health_path: Path
    output_data_path: Path
    output_alert_path: Path
    output_rail_wear_path: Path
    alert_context_dir: Path
    profile_path: Path
    edge_sync_queue_path: Path


@dataclass(frozen=True)
class LocalStackConfig:
    elevator_id: str = "elevator-001"
    serial_port: str = "/dev/ttyUSB0"
    baud: int = 115200
    addr: str = "0x50"
    sample_hz: float = 40.0
    detect_hz: int = 40
    reg_count: int = 13
    api_host: str = "0.0.0.0"
    api_port: int = 8085
    site_id: str = "local-site"
    site_name: str = "Local Control Panel"
    device_id: str = ""
    api_token: str = ""
    project_root: Path = _project_root()


def build_local_stack_paths(project_root: Path, elevator_id: str) -> LocalStackPaths:
    root = Path(project_root).expanduser().resolve()
    safe_elevator = _safe_token(elevator_id)
    run_dir = root / "run" / "local_stack"
    log_dir = root / "logs" / "local_stack"
    data_dir = root / "data" / "local_stack" / safe_elevator
    return LocalStackPaths(
        run_dir=run_dir,
        state_path=run_dir / "state.json",
        api_pid_path=run_dir / "api.pid",
        monitor_pid_path=run_dir / "monitor.pid",
        api_log_path=log_dir / "api.log",
        monitor_log_path=log_dir / f"{safe_elevator}.monitor.log",
        data_dir=data_dir,
        health_path=data_dir / "monitor_health.json",
        output_data_path=data_dir / "elevator_rt_live.csv",
        output_alert_path=data_dir / "elevator_alerts_live.csv",
        output_rail_wear_path=data_dir / "rail_wear_alerts_live.csv",
        alert_context_dir=data_dir / "alert_context",
        profile_path=data_dir / "profile.json",
        edge_sync_queue_path=data_dir / "edge_sync_queue.sqlite3",
    )


def build_api_command(config: LocalStackConfig, *, python_executable: str | None = None) -> list[str]:
    python_cmd = python_executable or sys.executable
    return [
        python_cmd,
        "-m",
        "elevator_monitor.api.main",
        "--host",
        str(config.api_host),
        "--port",
        str(max(1, int(config.api_port))),
    ]


def build_monitor_command(
    config: LocalStackConfig,
    *,
    python_executable: str | None = None,
    paths: LocalStackPaths | None = None,
) -> list[str]:
    python_cmd = python_executable or sys.executable
    resolved_paths = paths or build_local_stack_paths(config.project_root, config.elevator_id)
    edge_device_id = str(config.device_id or "").strip() or str(config.elevator_id)
    api_base_url = f"http://127.0.0.1:{max(1, int(config.api_port))}"
    command = [
        python_cmd,
        "-m",
        "elevator_monitor.realtime_monitor",
        "--elevator-id",
        str(config.elevator_id),
        "--port",
        str(config.serial_port),
        "--baud",
        str(int(config.baud)),
        "--addr",
        str(config.addr),
        "--sample-hz",
        str(float(config.sample_hz)),
        "--detect-hz",
        str(int(config.detect_hz)),
        "--reg-count",
        str(int(config.reg_count)),
        "--output-data",
        str(resolved_paths.output_data_path),
        "--output-alert",
        str(resolved_paths.output_alert_path),
        "--output-rail-wear-alert",
        str(resolved_paths.output_rail_wear_path),
        "--health-path",
        str(resolved_paths.health_path),
        "--log-file",
        str(resolved_paths.monitor_log_path),
        "--alert-context-dir",
        str(resolved_paths.alert_context_dir),
        "--profile-path",
        str(resolved_paths.profile_path),
        "--edge-sync-enabled",
        "--edge-sync-base-url",
        api_base_url,
        "--edge-sync-site-id",
        str(config.site_id),
        "--edge-sync-site-name",
        str(config.site_name),
        "--edge-sync-device-id",
        edge_device_id,
        "--edge-sync-queue-path",
        str(resolved_paths.edge_sync_queue_path),
    ]
    if str(config.api_token or "").strip():
        command.extend(["--edge-sync-api-token", str(config.api_token).strip()])
    return command


def _ensure_parent_dirs(paths: LocalStackPaths) -> None:
    paths.run_dir.mkdir(parents=True, exist_ok=True)
    paths.api_log_path.parent.mkdir(parents=True, exist_ok=True)
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    paths.alert_context_dir.mkdir(parents=True, exist_ok=True)


def _read_pid(pid_path: Path) -> int | None:
    try:
        text = pid_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except Exception:
        return None
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _write_pid(pid_path: Path, pid: int) -> None:
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(f"{int(pid)}\n", encoding="utf-8")


def _is_process_running(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _remove_if_exists(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _terminate_process(pid: int | None, *, timeout_s: float = 5.0) -> bool:
    if not _is_process_running(pid):
        return True
    try:
        os.kill(int(pid), signal.SIGTERM)
    except OSError:
        return False
    deadline = time.time() + max(0.5, float(timeout_s))
    while time.time() < deadline:
        if not _is_process_running(pid):
            return True
        time.sleep(0.1)
    try:
        os.kill(int(pid), signal.SIGKILL)
    except OSError:
        return not _is_process_running(pid)
    time.sleep(0.1)
    return not _is_process_running(pid)


def _wait_for_port(host: str, port: int, *, timeout_s: float = 10.0) -> None:
    deadline = time.time() + max(1.0, float(timeout_s))
    while time.time() < deadline:
        try:
            with socket.create_connection((host, int(port)), timeout=0.5):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"api did not become ready on {host}:{port} within {timeout_s:.1f}s")


def _spawn(command: list[str], *, cwd: Path, log_path: Path) -> subprocess.Popen[bytes]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fp = log_path.open("ab")
    return subprocess.Popen(
        command,
        cwd=str(cwd),
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def _load_state(state_path: Path) -> dict[str, object]:
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _state_payload(config: LocalStackConfig, paths: LocalStackPaths, *, api_pid: int, monitor_pid: int) -> dict[str, object]:
    payload = asdict(config)
    payload["project_root"] = str(config.project_root)
    payload["paths"] = {key: str(value) for key, value in asdict(paths).items()}
    payload["api_pid"] = int(api_pid)
    payload["monitor_pid"] = int(monitor_pid)
    payload["started_at_ms"] = int(time.time() * 1000)
    payload["api_url"] = f"http://127.0.0.1:{max(1, int(config.api_port))}"
    payload["panel_url"] = f"http://127.0.0.1:{max(1, int(config.api_port))}/panel"
    return payload


def start_local_stack(config: LocalStackConfig, *, python_executable: str | None = None) -> dict[str, object]:
    paths = build_local_stack_paths(config.project_root, config.elevator_id)
    _ensure_parent_dirs(paths)

    running_api_pid = _read_pid(paths.api_pid_path)
    running_monitor_pid = _read_pid(paths.monitor_pid_path)
    if _is_process_running(running_api_pid) or _is_process_running(running_monitor_pid):
        raise RuntimeError("local stack already running; use `status` or `stop` first")

    _remove_if_exists(paths.api_pid_path)
    _remove_if_exists(paths.monitor_pid_path)

    api_process = _spawn(
        build_api_command(config, python_executable=python_executable),
        cwd=config.project_root,
        log_path=paths.api_log_path,
    )
    try:
        if api_process.poll() is not None:
            raise RuntimeError(f"api exited early; check {paths.api_log_path}")
        _wait_for_port("127.0.0.1", int(config.api_port), timeout_s=10.0)

        monitor_process = _spawn(
            build_monitor_command(config, python_executable=python_executable, paths=paths),
            cwd=config.project_root,
            log_path=paths.monitor_log_path,
        )
        time.sleep(0.8)
        if monitor_process.poll() is not None:
            raise RuntimeError(f"monitor exited early; check {paths.monitor_log_path}")
    except Exception:
        _terminate_process(api_process.pid)
        _remove_if_exists(paths.api_pid_path)
        _remove_if_exists(paths.monitor_pid_path)
        _remove_if_exists(paths.state_path)
        raise

    _write_pid(paths.api_pid_path, api_process.pid)
    _write_pid(paths.monitor_pid_path, monitor_process.pid)
    state = _state_payload(config, paths, api_pid=api_process.pid, monitor_pid=monitor_process.pid)
    paths.state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return state


def stop_local_stack(project_root: Path | None = None) -> dict[str, object]:
    root = Path(project_root or _project_root()).expanduser().resolve()
    run_dir = root / "run" / "local_stack"
    state_path = run_dir / "state.json"
    api_pid_path = run_dir / "api.pid"
    monitor_pid_path = run_dir / "monitor.pid"

    api_pid = _read_pid(api_pid_path)
    monitor_pid = _read_pid(monitor_pid_path)
    monitor_stopped = _terminate_process(monitor_pid)
    api_stopped = _terminate_process(api_pid)
    _remove_if_exists(api_pid_path)
    _remove_if_exists(monitor_pid_path)
    _remove_if_exists(state_path)
    return {
        "api_pid": api_pid,
        "monitor_pid": monitor_pid,
        "api_stopped": bool(api_stopped),
        "monitor_stopped": bool(monitor_stopped),
    }


def local_stack_status(project_root: Path | None = None) -> dict[str, object]:
    root = Path(project_root or _project_root()).expanduser().resolve()
    paths = LocalStackPaths(
        run_dir=root / "run" / "local_stack",
        state_path=root / "run" / "local_stack" / "state.json",
        api_pid_path=root / "run" / "local_stack" / "api.pid",
        monitor_pid_path=root / "run" / "local_stack" / "monitor.pid",
        api_log_path=root / "logs" / "local_stack" / "api.log",
        monitor_log_path=root / "logs" / "local_stack" / "monitor.log",
        data_dir=root / "data" / "local_stack",
        health_path=root / "data" / "local_stack" / "monitor_health.json",
        output_data_path=root / "data" / "local_stack" / "elevator_rt_live.csv",
        output_alert_path=root / "data" / "local_stack" / "elevator_alerts_live.csv",
        output_rail_wear_path=root / "data" / "local_stack" / "rail_wear_alerts_live.csv",
        alert_context_dir=root / "data" / "local_stack" / "alert_context",
        profile_path=root / "data" / "local_stack" / "profile.json",
        edge_sync_queue_path=root / "data" / "local_stack" / "edge_sync_queue.sqlite3",
    )
    state = _load_state(paths.state_path)
    api_pid = _read_pid(paths.api_pid_path)
    monitor_pid = _read_pid(paths.monitor_pid_path)
    return {
        "configured": bool(state),
        "state": state,
        "api_pid": api_pid,
        "monitor_pid": monitor_pid,
        "api_running": _is_process_running(api_pid),
        "monitor_running": _is_process_running(monitor_pid),
        "panel_url": state.get("panel_url", ""),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start or stop a local control-panel stack")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start", help="start api + realtime monitor")
    start_parser.add_argument("--elevator-id", default="elevator-001", help="电梯 ID")
    start_parser.add_argument("--serial-port", default="/dev/ttyUSB0", help="实时监控串口")
    start_parser.add_argument("--baud", type=int, default=115200, help="串口波特率")
    start_parser.add_argument("--addr", default="0x50", help="设备地址，支持 0x 前缀")
    start_parser.add_argument("--sample-hz", type=float, default=40.0, help="采样频率")
    start_parser.add_argument("--detect-hz", type=int, default=40, help="设备检测频率")
    start_parser.add_argument("--reg-count", type=int, default=13, help="寄存器数量")
    start_parser.add_argument("--api-host", default="0.0.0.0", help="API 绑定地址")
    start_parser.add_argument("--api-port", type=int, default=8085, help="API 绑定端口")
    start_parser.add_argument("--site-id", default="local-site", help="边缘上报 site_id")
    start_parser.add_argument("--site-name", default="Local Control Panel", help="边缘上报 site_name")
    start_parser.add_argument("--device-id", default="", help="边缘上报 device_id，默认复用 elevator_id")
    start_parser.add_argument("--api-token", default="", help="本地 API token，可选")

    subparsers.add_parser("stop", help="stop local stack")
    subparsers.add_parser("status", help="show local stack status")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    if args.command == "start":
        config = LocalStackConfig(
            elevator_id=str(args.elevator_id),
            serial_port=str(args.serial_port),
            baud=int(args.baud),
            addr=str(args.addr),
            sample_hz=float(args.sample_hz),
            detect_hz=int(args.detect_hz),
            reg_count=int(args.reg_count),
            api_host=str(args.api_host),
            api_port=int(args.api_port),
            site_id=str(args.site_id),
            site_name=str(args.site_name),
            device_id=str(args.device_id),
            api_token=str(args.api_token),
            project_root=_project_root(),
        )
        state = start_local_stack(config)
        print(f"panel: {state['panel_url']}")
        print(f"api log: {state['paths']['api_log_path']}")
        print(f"monitor log: {state['paths']['monitor_log_path']}")
        return 0
    if args.command == "stop":
        result = stop_local_stack()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    status = local_stack_status()
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
