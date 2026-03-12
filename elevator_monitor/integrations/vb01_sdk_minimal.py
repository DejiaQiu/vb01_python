from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from typing import Any, Callable

from ..common import REG_MAP
from ..device_model import DeviceModel


@dataclass
class SDKMinimalProbeConfig:
    port: str = "/dev/ttyUSB0"
    baud: int = 115200
    addr: int = 0x50
    reg_addr: int = 0x34
    reg_count: int = 19
    sample_hz: float = 5.0
    startup_timeout_s: float = 10.0
    duration_s: float = 20.0
    device_name: str = "vb01-sdk-minimal"


def _parse_int_auto(value: str) -> int:
    return int(value, 0)


def _build_device(config: SDKMinimalProbeConfig, device_factory: Callable[..., Any]):
    # Keep compatibility with both old SDK signature and current project signature.
    try:
        return device_factory(config.device_name, config.port, int(config.baud), int(config.addr), False)
    except TypeError:
        return device_factory(config.device_name, config.port, int(config.baud), int(config.addr))


def _start_loop(device: Any, *, reg_addr: int, reg_count: int, sample_hz: float) -> None:
    period_s = 1.0 / max(1.0, float(sample_hz))
    try:
        device.startLoopRead(regAddr=int(reg_addr), regCount=max(1, int(reg_count)), period_s=period_s)
    except TypeError:
        device.startLoopRead()


def _has_core_data(device: Any) -> bool:
    for reg in ("52", "53", "54"):
        value = device.get(reg)
        if value is not None:
            return True
    return False


def _read_snapshot(device: Any) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for field, reg in REG_MAP.items():
        snapshot[field] = device.get(reg)
    return snapshot


def run_sdk_minimal_probe(
    config: SDKMinimalProbeConfig,
    *,
    device_factory: Callable[..., Any] = DeviceModel,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    device = _build_device(config, device_factory)
    opened = False
    samples: list[dict[str, Any]] = []

    try:
        opened_result = device.openDevice()
        opened = opened_result is not False
        if not opened:
            return {
                "ok": False,
                "status": "open_failed",
                "message": f"open failed port={config.port} baud={config.baud} addr={hex(int(config.addr))}",
                "config": vars(config),
                "sample_count": 0,
                "samples_preview": [],
            }

        _start_loop(
            device,
            reg_addr=config.reg_addr,
            reg_count=config.reg_count,
            sample_hz=config.sample_hz,
        )

        started = monotonic()
        deadline = started + max(0.1, float(config.startup_timeout_s))
        while monotonic() < deadline:
            if _has_core_data(device):
                break
            sleeper(0.02)
        else:
            return {
                "ok": False,
                "status": "startup_timeout",
                "message": f"no first frame within {float(config.startup_timeout_s):.2f}s",
                "config": vars(config),
                "sample_count": 0,
                "samples_preview": [],
            }

        read_started = monotonic()
        duration_s = max(0.1, float(config.duration_s))
        read_period_s = 1.0 / max(1.0, float(config.sample_hz))
        while monotonic() - read_started < duration_s:
            snapshot = _read_snapshot(device)
            if any(snapshot.get(k) is not None for k in ("Ax", "Ay", "Az")):
                samples.append(snapshot)
            sleeper(read_period_s)

        preview = samples[:3]
        return {
            "ok": len(samples) > 0,
            "status": "ok" if samples else "no_samples",
            "message": "sdk minimal probe completed",
            "config": vars(config),
            "sample_count": len(samples),
            "samples_preview": preview,
        }
    finally:
        if opened:
            try:
                device.stopLoopRead()
            except Exception:
                pass
            try:
                device.closeDevice()
            except Exception:
                pass


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="VB01 官方SDK兼容最小测试单元")
    parser.add_argument("--port", default="/dev/ttyUSB0", help="串口设备路径")
    parser.add_argument("--baud", type=int, default=115200, help="波特率（项目默认 115200）")
    parser.add_argument("--addr", type=_parse_int_auto, default=0x50, help="设备地址，支持0x前缀")
    parser.add_argument("--reg-addr", type=_parse_int_auto, default=0x34, help="轮询起始寄存器")
    parser.add_argument("--reg-count", type=int, default=19, help="轮询寄存器数量（官方SDK默认19）")
    parser.add_argument("--sample-hz", type=float, default=5.0, help="轮询频率（官方SDK默认约5Hz）")
    parser.add_argument("--startup-timeout-s", type=float, default=10.0, help="首帧超时")
    parser.add_argument("--duration-s", type=float, default=20.0, help="采样持续时长")
    parser.add_argument("--pretty", action="store_true", help="格式化输出")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    config = SDKMinimalProbeConfig(
        port=args.port,
        baud=args.baud,
        addr=args.addr,
        reg_addr=args.reg_addr,
        reg_count=args.reg_count,
        sample_hz=args.sample_hz,
        startup_timeout_s=args.startup_timeout_s,
        duration_s=args.duration_s,
    )
    payload = run_sdk_minimal_probe(config)
    if args.pretty:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
