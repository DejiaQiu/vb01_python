from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from elevator_monitor.data_recorder import DataRecorder
from elevator_monitor.realtime_vibration import RealtimeVibrationReader, VIBRATION_FIELDS
from elevator_monitor.runtime_config import DEFAULT_DEVICE_NAME


def _parse_int_auto(value: str) -> int:
    return int(value, 0)


def _default_output_path() -> Path:
    ts = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    return Path("data") / "captures" / f"vibration_30s_{ts}.csv"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="采集振动传感器 30 秒数据并保存到本地 CSV")
    parser.add_argument("--elevator-id", default="elevator-001", help="电梯唯一 ID")
    parser.add_argument("--device-name", default=DEFAULT_DEVICE_NAME, help="设备名称")
    parser.add_argument("--port", default="/dev/ttyUSB0", help="串口设备路径")
    parser.add_argument("--baud", type=int, default=115200, help="波特率")
    parser.add_argument("--addr", type=_parse_int_auto, default=0x50, help="设备地址，支持 0x 前缀")
    parser.add_argument("--sample-hz", type=float, default=100.0, help="轮询采样频率")
    parser.add_argument("--detect-hz", type=int, default=100, help="设备检测周期，写入寄存器 0x65")
    parser.add_argument("--no-set-detect-hz", action="store_true", help="不写设备检测周期")
    parser.add_argument("--duration-s", type=float, default=30.0, help="采集时长（秒）")
    parser.add_argument("--startup-timeout-s", type=float, default=3.0, help="连接后等待首帧超时（秒）")
    parser.add_argument("--max-idle-s", type=float, default=8.0, help="连续无新帧超时（秒）")
    parser.add_argument("--poll-s", type=float, default=0.01, help="轮询间隔（秒）")
    parser.add_argument("--max-data-age-ms", type=int, default=500, help="可接受数据最大延迟（毫秒）")
    parser.add_argument("--reg-addr", type=_parse_int_auto, default=0x34, help="循环读取起始寄存器")
    parser.add_argument("--reg-count", type=int, default=19, help="循环读取寄存器数量")
    parser.add_argument("--emit-mode", choices=["new", "fixed"], default="fixed", help="new=仅写新帧；fixed=按固定频率写出（默认）")
    parser.add_argument("--emit-hz", type=float, default=100.0, help="fixed 模式输出频率")
    parser.add_argument("--output", default="", help="输出 CSV 路径，默认写入 data/captures/")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    output_path = Path(args.output) if args.output else _default_output_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    reader = RealtimeVibrationReader(
        elevator_id=args.elevator_id,
        device_name=args.device_name,
        port=args.port,
        baud=args.baud,
        addr=args.addr,
        sample_hz=args.sample_hz,
        detect_hz=args.detect_hz,
        no_set_detect_hz=args.no_set_detect_hz,
        max_data_age_ms=args.max_data_age_ms,
        reg_addr=args.reg_addr,
        reg_count=args.reg_count,
    )

    if not reader.connect():
        print(
            f"connect failed: port={args.port} baud={args.baud} addr={hex(args.addr)}",
            file=sys.stderr,
        )
        return 1

    if args.startup_timeout_s > 0 and reader.device is not None:
        got_first = reader.device.wait_for_data(timeout_s=max(0.0, float(args.startup_timeout_s)))
        if not got_first:
            reader.close()
            print(
                f"startup timeout: no first frame within {float(args.startup_timeout_s):.2f}s",
                file=sys.stderr,
            )
            return 2

    recorder = DataRecorder(
        str(output_path),
        file_format="csv",
        fieldnames=VIBRATION_FIELDS,
        flush=True,
    ).open()

    captured = 0
    started = time.monotonic()
    exit_code = 0
    try:
        if args.emit_mode == "fixed":
            frame_iter = reader.iter_frames_fixed_rate(
                emit_hz=args.emit_hz,
                duration_s=args.duration_s,
                max_idle_s=args.max_idle_s,
            )
        else:
            frame_iter = reader.iter_frames(
                duration_s=args.duration_s,
                poll_s=args.poll_s,
                max_idle_s=args.max_idle_s,
            )

        for frame in frame_iter:
            if "is_new_frame" not in frame:
                frame["is_new_frame"] = 1
            print(json.dumps(frame, ensure_ascii=False), flush=True)
            recorder.write(frame)
            captured += 1
    except KeyboardInterrupt:
        print("stopped by user", file=sys.stderr)
        exit_code = 130
    except TimeoutError as ex:
        print(str(ex), file=sys.stderr)
        exit_code = 2
    finally:
        recorder.close()
        reader.close()

    if exit_code != 0:
        return exit_code
    if captured == 0:
        print("no vibration frame captured", file=sys.stderr)
        return 2

    elapsed_s = time.monotonic() - started
    print(f"captured={captured} duration_s={elapsed_s:.2f} output={output_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
