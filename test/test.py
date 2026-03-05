import argparse
import json
import sys
import time
from pathlib import Path

import device_model

# Ensure project modules are importable when running `python test/test.py`.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from elevator_monitor.device_model import DeviceModel as ProjectDeviceModel
except Exception:
    ProjectDeviceModel = None


REG_MAP = {
    "Ax": "52",
    "Ay": "53",
    "Az": "54",
    "Gx": "55",
    "Gy": "56",
    "Gz": "57",
    "vx": "58",
    "vy": "59",
    "vz": "60",
    "ax": "61",
    "ay": "62",
    "az": "63",
    "t": "64",
    "sx": "65",
    "sy": "66",
    "sz": "67",
    "fx": "68",
    "fy": "69",
    "fz": "70",
}


def _parse_int_auto(value: str) -> int:
    return int(value, 0)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="WTVB01-485 最小采集脚本：固定频率打印数据")
    parser.add_argument("--port", default="/dev/ttyUSB0", help="串口设备路径")
    parser.add_argument("--baud", type=int, default=115200, help="波特率")
    parser.add_argument("--addr", type=_parse_int_auto, default=0x50, help="设备地址，支持0x前缀")
    parser.add_argument(
        "--device-model",
        choices=["project", "sdk"],
        default="project",
        help="project=项目增强版设备模型（推荐）；sdk=官方SDK风格模型",
    )
    parser.add_argument("--sample-hz", type=float, default=5.0, help="打印频率（5Hz=0.2s）")
    parser.add_argument("--reg-addr", type=_parse_int_auto, default=0x34, help="轮询起始寄存器")
    parser.add_argument("--reg-count", type=int, default=19, help="轮询寄存器个数")
    parser.add_argument("--detect-hz", type=int, default=5, help="写入寄存器0x65（检测周期Hz）")
    parser.add_argument("--no-set-detect-hz", action="store_true", help="不写寄存器0x65")
    parser.add_argument("--reconnect-no-data-s", type=float, default=20.0, help="超过该秒数无新帧则重连")
    parser.add_argument("--startup-timeout-s", type=float, default=5.0, help="启动后等待首帧超时秒数")
    parser.add_argument("--duration-s", type=float, default=60.0, help="采集时长（秒）")
    return parser


def _get_data_ts_ms(dev) -> int | None:
    if hasattr(dev, "getLastUpdateTsMs"):
        return dev.getLastUpdateTsMs()
    if hasattr(dev, "get_last_update_ts_ms"):
        return dev.get_last_update_ts_ms()
    return None


def _resolve_device_class(model_name: str):
    if model_name == "project":
        if ProjectDeviceModel is None:
            raise RuntimeError("project 设备模型不可用，请改用 --device-model sdk")
        return ProjectDeviceModel
    return device_model.DeviceModel


def _connect_device(args: argparse.Namespace):
    model_cls = _resolve_device_class(args.device_model)
    try:
        dev = model_cls("测试设备", args.port, int(args.baud), int(args.addr), False)
    except TypeError:
        dev = model_cls("测试设备", args.port, int(args.baud), int(args.addr))

    opened = dev.openDevice()
    if opened is False:
        raise RuntimeError(f"open failed: port={args.port} baud={args.baud}")

    if not args.no_set_detect_hz:
        dev.writeReg(0x65, int(args.detect_hz))
    period_s = 1.0 / max(1.0, float(args.sample_hz))
    dev.startLoopRead(
        regAddr=int(args.reg_addr),
        regCount=max(1, int(args.reg_count)),
        period_s=period_s,
    )
    deadline = time.monotonic() + max(0.1, float(args.startup_timeout_s))
    while time.monotonic() < deadline:
        if _get_data_ts_ms(dev) is not None:
            return dev
        time.sleep(0.01)
    _close_device(dev)
    raise TimeoutError(f"startup timeout: no first frame within {float(args.startup_timeout_s):.2f}s")


def _close_device(dev) -> None:
    try:
        dev.stopLoopRead()
    except Exception:
        pass
    try:
        dev.closeDevice()
    except Exception:
        pass


def _snapshot(dev, sample_idx: int, ts_ms: int, data_ts_ms: int | None, is_new: int) -> dict:
    row = {
        "sample_idx": sample_idx,
        "ts_ms": ts_ms,
        "data_ts_ms": data_ts_ms if data_ts_ms is not None else "",
        "data_age_ms": (ts_ms - data_ts_ms) if data_ts_ms is not None else "",
        "is_new_frame": is_new,
    }
    for field, reg in REG_MAP.items():
        row[field] = dev.get(reg)
    return row


def main() -> int:
    args = build_arg_parser().parse_args()

    device = None
    sample_idx = 0
    reconnect_count = 0
    last_data_ts_ms = None
    last_new_monotonic = time.monotonic()
    try:
        device = _connect_device(args)
        deadline = time.monotonic() + max(0.1, float(args.duration_s))
        fixed_period_s = 1.0 / max(1.0, float(args.sample_hz))
        next_t = time.monotonic()

        while time.monotonic() < deadline:
            sleep_s = next_t - time.monotonic()
            if sleep_s > 0:
                time.sleep(sleep_s)
            else:
                next_t = time.monotonic()

            now_mono = time.monotonic()
            ts_ms = int(time.time() * 1000)
            data_ts_ms = _get_data_ts_ms(device)
            is_new = 1 if data_ts_ms is not None and data_ts_ms != last_data_ts_ms else 0

            if is_new:
                last_new_monotonic = now_mono
                last_data_ts_ms = data_ts_ms

            row = _snapshot(device, sample_idx, ts_ms, data_ts_ms, is_new)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            sample_idx += 1

            if time.monotonic() - last_new_monotonic > max(0.5, float(args.reconnect_no_data_s)):
                _close_device(device)
                reconnect_count += 1
                device = _connect_device(args)
                last_data_ts_ms = None
                last_new_monotonic = time.monotonic()
                next_t = time.monotonic()
            else:
                next_t += fixed_period_s
    except TimeoutError as ex:
        print(str(ex))
        return 2
    finally:
        if device is not None:
            _close_device(device)

    print(f"printed_rows={sample_idx} reconnects={reconnect_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


