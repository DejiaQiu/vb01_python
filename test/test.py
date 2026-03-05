import argparse
import csv
import os
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


CSV_FIELDS = [
    "sample_idx",
    "ts_ms",
    "data_ts_ms",
    "data_age_ms",
    "is_new_frame",
    "Ax",
    "Ay",
    "Az",
    "Gx",
    "Gy",
    "Gz",
    "vx",
    "vy",
    "vz",
    "ax",
    "ay",
    "az",
    "t",
    "sx",
    "sy",
    "sz",
    "fx",
    "fy",
    "fz",
]

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


def _default_output() -> str:
    ts = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    return f"data/captures/sdk_capture_{ts}.csv"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="WTVB01-485 最小采集脚本：写CSV，不打印实时数据")
    parser.add_argument("--port", default="/dev/ttyUSB0", help="串口设备路径")
    parser.add_argument("--baud", type=int, default=115200, help="波特率")
    parser.add_argument("--addr", type=_parse_int_auto, default=0x50, help="设备地址，支持0x前缀")
    parser.add_argument(
        "--device-model",
        choices=["project", "sdk"],
        default="project",
        help="project=项目增强版设备模型（推荐）；sdk=官方SDK风格模型",
    )
    parser.add_argument("--sample-hz", type=float, default=100.0, help="轮询频率（100Hz=0.01s）")
    parser.add_argument("--reg-addr", type=_parse_int_auto, default=0x34, help="轮询起始寄存器")
    parser.add_argument("--reg-count", type=int, default=13, help="轮询寄存器个数（100Hz推荐13）")
    parser.add_argument("--detect-hz", type=int, default=100, help="写入寄存器0x65（检测周期Hz）")
    parser.add_argument("--no-set-detect-hz", action="store_true", help="不写寄存器0x65")
    parser.add_argument("--emit-mode", choices=["new", "fixed"], default="new", help="new=只写新帧；fixed=按固定频率写")
    parser.add_argument("--emit-hz", type=float, default=100.0, help="fixed 模式写出频率")
    parser.add_argument("--poll-s", type=float, default=0.001, help="new 模式轮询间隔")
    parser.add_argument("--reconnect-no-data-s", type=float, default=20.0, help="超过该秒数无新帧则重连")
    parser.add_argument("--startup-timeout-s", type=float, default=15.0, help="启动后等待首帧超时秒数")
    parser.add_argument("--min-accept-hz", type=float, default=50.0, help="可接受最小真实频率")
    parser.add_argument("--max-accept-hz", type=float, default=99.0, help="可接受最大真实频率")
    parser.add_argument("--strict-hz-range", action="store_true", help="频率不在[min,max]时返回非零退出码")
    parser.add_argument("--flush-rows", type=int, default=50, help="每写N行执行一次flush；1表示每行落盘")
    parser.add_argument("--fsync", action="store_true", help="flush后追加fsync（更稳但更慢）")
    parser.add_argument("--duration-s", type=float, default=60.0, help="采集时长（秒）")
    parser.add_argument("--output", default=_default_output(), help="CSV输出路径")
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


def _snapshot(dev: device_model.DeviceModel, sample_idx: int, ts_ms: int, data_ts_ms: int | None, is_new: int) -> dict:
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
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    device = None
    sample_idx = 0
    reconnect_count = 0
    last_data_ts_ms = None
    seen_data_ts_ms: set[int] = set()
    first_data_ts_ms: int | None = None
    last_seen_data_ts_ms: int | None = None
    last_new_monotonic = time.monotonic()
    start_mono = time.monotonic()
    try:
        device = _connect_device(args)
        deadline = time.monotonic() + max(0.1, float(args.duration_s))
        fixed_period_s = 1.0 / max(1.0, float(args.emit_hz))
        next_fixed_t = time.monotonic()

        with out_path.open("w", encoding="utf-8", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=CSV_FIELDS)
            writer.writeheader()
            while time.monotonic() < deadline:
                now_mono = time.monotonic()
                ts_ms = int(time.time() * 1000)
                data_ts_ms = _get_data_ts_ms(device)
                is_new = 1 if data_ts_ms is not None and data_ts_ms != last_data_ts_ms else 0

                if is_new:
                    last_new_monotonic = now_mono
                    last_data_ts_ms = data_ts_ms
                    if data_ts_ms is not None:
                        dts = int(data_ts_ms)
                        seen_data_ts_ms.add(dts)
                        last_seen_data_ts_ms = dts
                        if first_data_ts_ms is None:
                            first_data_ts_ms = dts

                if args.emit_mode == "new":
                    if is_new:
                        writer.writerow(_snapshot(device, sample_idx, ts_ms, data_ts_ms, 1))
                        sample_idx += 1
                        if args.flush_rows > 0 and sample_idx % args.flush_rows == 0:
                            fp.flush()
                            if args.fsync:
                                os.fsync(fp.fileno())
                    else:
                        time.sleep(max(0.0005, float(args.poll_s)))
                else:
                    # fixed mode: always write, mark whether the source frame is new.
                    writer.writerow(_snapshot(device, sample_idx, ts_ms, data_ts_ms, is_new))
                    sample_idx += 1
                    if args.flush_rows > 0 and sample_idx % args.flush_rows == 0:
                        fp.flush()
                        if args.fsync:
                            os.fsync(fp.fileno())
                    next_fixed_t += fixed_period_s
                    sleep_s = next_fixed_t - time.monotonic()
                    if sleep_s > 0:
                        time.sleep(sleep_s)
                    else:
                        next_fixed_t = time.monotonic()

                if time.monotonic() - last_new_monotonic > max(0.5, float(args.reconnect_no_data_s)):
                    _close_device(device)
                    reconnect_count += 1
                    device = _connect_device(args)
                    last_data_ts_ms = None
                    last_new_monotonic = time.monotonic()
            fp.flush()
            if args.fsync:
                os.fsync(fp.fileno())
    except TimeoutError as ex:
        print(str(ex))
        return 2
    finally:
        if device is not None:
            _close_device(device)

    elapsed_s = max(0.001, time.monotonic() - start_mono)
    unique_frames = len(seen_data_ts_ms)
    effective_hz = 0.0
    if first_data_ts_ms is not None and last_seen_data_ts_ms is not None and last_seen_data_ts_ms > first_data_ts_ms:
        effective_hz = (unique_frames - 1) / ((last_seen_data_ts_ms - first_data_ts_ms) / 1000.0)
    elif unique_frames > 0:
        effective_hz = unique_frames / elapsed_s

    in_range = float(args.min_accept_hz) <= effective_hz <= float(args.max_accept_hz)
    print(
        f"saved={out_path} rows={sample_idx} unique_frames={unique_frames} "
        f"effective_hz={effective_hz:.2f} reconnects={reconnect_count} in_range={int(in_range)}"
    )
    if args.strict_hz_range and not in_range:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


