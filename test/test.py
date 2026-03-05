import argparse
import csv
import time
from pathlib import Path

import device_model


CSV_FIELDS = [
    "sample_idx",
    "ts_ms",
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
    parser.add_argument("--sample-hz", type=float, default=100.0, help="轮询频率（100Hz=0.01s）")
    parser.add_argument("--reg-addr", type=_parse_int_auto, default=0x34, help="轮询起始寄存器")
    parser.add_argument("--reg-count", type=int, default=19, help="轮询寄存器个数")
    parser.add_argument("--detect-hz", type=int, default=100, help="写入寄存器0x65（检测周期Hz）")
    parser.add_argument("--no-set-detect-hz", action="store_true", help="不写寄存器0x65")
    parser.add_argument("--duration-s", type=float, default=60.0, help="采集时长（秒）")
    parser.add_argument("--output", default=_default_output(), help="CSV输出路径")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    device = device_model.DeviceModel("测试设备", args.port, int(args.baud), int(args.addr))
    sample_idx = 0
    try:
        device.openDevice()
        # Configure device output rate first, then start polling.
        if not args.no_set_detect_hz:
            device.writeReg(0x65, int(args.detect_hz))

        period_s = 1.0 / max(1.0, float(args.sample_hz))
        device.startLoopRead(
            regAddr=int(args.reg_addr),
            regCount=max(1, int(args.reg_count)),
            period_s=period_s,
        )
        time.sleep(0.5)

        deadline = time.monotonic() + max(0.1, float(args.duration_s))

        with out_path.open("w", encoding="utf-8", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=CSV_FIELDS)
            writer.writeheader()
            while time.monotonic() < deadline:
                row = {
                    "sample_idx": sample_idx,
                    "ts_ms": int(time.time() * 1000),
                }
                for field, reg in REG_MAP.items():
                    row[field] = device.get(reg)
                writer.writerow(row)
                sample_idx += 1
                time.sleep(period_s)
    finally:
        device.stopLoopRead()
        device.closeDevice()

    print(f"saved={out_path} rows={sample_idx}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


