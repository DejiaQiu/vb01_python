from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any, Optional

from .common import CORE_FIELDS, REG_MAP, parse_float, vector_magnitude
from .data_recorder import DataRecorder, format_ts_ms, now_ts_ms
from .device_model import DeviceModel
from .runtime_config import DEFAULT_DEVICE_NAME, env_float, env_int, env_str

VIBRATION_FIELDS = [
    "elevator_id",
    "ts_ms",
    "ts",
    "data_ts_ms",
    "data_age_ms",
    "is_new_frame",
    *REG_MAP.keys(),
    "A_mag",
    "G_mag",
]


def _parse_int_auto(value: str) -> int:
    return int(value, 0)


def build_vibration_frame(
    *,
    device: DeviceModel,
    elevator_id: str,
    ts_ms: Optional[int] = None,
    max_data_age_ms: Optional[int] = 500,
) -> Optional[dict[str, Any]]:
    if ts_ms is None:
        ts_ms = now_ts_ms()
    ts_ms = int(ts_ms)

    data_ts_ms = device.get_last_update_ts_ms()
    if data_ts_ms is None:
        return None

    data_age_ms = ts_ms - int(data_ts_ms)
    if max_data_age_ms is not None and data_age_ms > max(0, int(max_data_age_ms)):
        return None

    snapshot = device.get_snapshot(REG_MAP.values())
    record: dict[str, Any] = {
        "elevator_id": elevator_id,
        "ts_ms": ts_ms,
        "ts": format_ts_ms(ts_ms),
        "data_ts_ms": int(data_ts_ms),
        "data_age_ms": int(data_age_ms),
    }
    for name, reg in REG_MAP.items():
        record[name] = snapshot.get(reg)

    has_core = any(record.get(k) is not None for k in CORE_FIELDS)
    if not has_core:
        return None

    ax = parse_float(record.get("Ax"))
    ay = parse_float(record.get("Ay"))
    az = parse_float(record.get("Az"))
    gx = parse_float(record.get("Gx"))
    gy = parse_float(record.get("Gy"))
    gz = parse_float(record.get("Gz"))
    record["A_mag"] = vector_magnitude(ax, ay, az)
    record["G_mag"] = vector_magnitude(gx, gy, gz)

    return record


class RealtimeVibrationReader:
    def __init__(
        self,
        *,
        elevator_id: str = "elevator-unknown",
        device_name: str = DEFAULT_DEVICE_NAME,
        port: str = "/dev/ttyUSB0",
        baud: int = 115200,
        addr: int = 0x50,
        sample_hz: float = 100.0,
        detect_hz: int = 100,
        no_set_detect_hz: bool = False,
        max_data_age_ms: int = 500,
        reg_addr: int = 0x34,
        reg_count: int = 19,
        device: Optional[DeviceModel] = None,
        owns_device: bool = True,
    ) -> None:
        self.elevator_id = elevator_id
        self.device_name = device_name
        self.port = port
        self.baud = int(baud)
        self.addr = int(addr)
        self.sample_hz = max(1.0, float(sample_hz))
        self.detect_hz = int(detect_hz)
        self.no_set_detect_hz = bool(no_set_detect_hz)
        self.max_data_age_ms = int(max_data_age_ms)
        self.reg_addr = int(reg_addr)
        self.reg_count = int(reg_count)

        self.device: Optional[DeviceModel] = device
        self._owns_device = bool(owns_device)
        self._last_data_ts_ms: Optional[int] = None

    def connect(self) -> bool:
        if self.device is not None:
            return True

        device = DeviceModel(
            self.device_name,
            self.port,
            self.baud,
            self.addr,
            verbose=False,
        )
        if not device.openDevice():
            return False

        if not self.no_set_detect_hz:
            try:
                device.writeReg(0x65, int(self.detect_hz))
            except Exception:
                pass

        try:
            device.startLoopRead(
                regAddr=self.reg_addr,
                regCount=self.reg_count,
                period_s=1.0 / self.sample_hz,
            )
        except Exception:
            try:
                device.closeDevice()
            except Exception:
                pass
            return False

        self.device = device
        self._owns_device = True
        return True

    def close(self) -> None:
        if self.device is None:
            return
        if self._owns_device:
            try:
                self.device.stopLoopRead()
            except Exception:
                pass
            try:
                self.device.closeDevice()
            except Exception:
                pass
        self.device = None
        self._last_data_ts_ms = None

    def read_latest(
        self,
        *,
        wait_timeout_s: float = 0.0,
        require_new: bool = True,
        ts_ms: Optional[int] = None,
    ) -> Optional[dict[str, Any]]:
        if self.device is None:
            raise RuntimeError("reader is not connected")

        if wait_timeout_s > 0:
            self.device.wait_for_data(timeout_s=max(0.0, float(wait_timeout_s)))

        frame = build_vibration_frame(
            device=self.device,
            elevator_id=self.elevator_id,
            ts_ms=ts_ms,
            max_data_age_ms=self.max_data_age_ms,
        )
        if frame is None:
            return None

        data_ts_ms = int(frame["data_ts_ms"])
        if require_new and self._last_data_ts_ms == data_ts_ms:
            return None

        self._last_data_ts_ms = data_ts_ms
        return frame

    def iter_frames(
        self,
        *,
        duration_s: Optional[float] = None,
        limit: Optional[int] = None,
        poll_s: float = 0.01,
        max_idle_s: Optional[float] = None,
    ):
        started = time.monotonic()
        last_emit = started
        emitted = 0
        poll_s = max(0.001, float(poll_s))
        idle_limit = None if max_idle_s is None else max(0.1, float(max_idle_s))

        while True:
            if duration_s is not None and (time.monotonic() - started) >= max(0.0, float(duration_s)):
                break
            if limit is not None and emitted >= max(0, int(limit)):
                break

            frame = self.read_latest(require_new=True)
            if frame is not None:
                emitted += 1
                last_emit = time.monotonic()
                yield frame
            else:
                if idle_limit is not None and (time.monotonic() - last_emit) >= idle_limit:
                    raise TimeoutError(f"no new vibration frame for {idle_limit:.1f}s")
                time.sleep(poll_s)

    def iter_frames_fixed_rate(
        self,
        *,
        emit_hz: float = 100.0,
        duration_s: Optional[float] = None,
        limit: Optional[int] = None,
        max_idle_s: Optional[float] = None,
    ):
        target_hz = max(1.0, float(emit_hz))
        period_s = 1.0 / target_hz
        started = time.monotonic()
        next_t = started
        emitted = 0
        last_frame: Optional[dict[str, Any]] = None
        last_source_ts: Optional[int] = None
        last_new_monotonic = started
        idle_limit = None if max_idle_s is None else max(0.1, float(max_idle_s))

        while True:
            if duration_s is not None and (time.monotonic() - started) >= max(0.0, float(duration_s)):
                break
            if limit is not None and emitted >= max(0, int(limit)):
                break

            now_mono = time.monotonic()
            sleep_s = next_t - now_mono
            if sleep_s > 0:
                time.sleep(sleep_s)
            else:
                next_t = now_mono

            ts_ms = now_ts_ms()
            frame = self.read_latest(require_new=False, ts_ms=ts_ms)
            is_new_frame = 0
            if frame is not None:
                src_ts = int(frame["data_ts_ms"])
                is_new_frame = 1 if last_source_ts != src_ts else 0
                if is_new_frame:
                    last_new_monotonic = time.monotonic()
                last_source_ts = src_ts
                frame["is_new_frame"] = is_new_frame
                last_frame = dict(frame)
            elif last_frame is not None:
                frame = dict(last_frame)
                frame["ts_ms"] = ts_ms
                frame["ts"] = format_ts_ms(ts_ms)
                data_ts_ms = int(frame.get("data_ts_ms", ts_ms))
                frame["data_age_ms"] = int(ts_ms - data_ts_ms)
                frame["is_new_frame"] = 0
            else:
                frame = None

            if idle_limit is not None and (time.monotonic() - last_new_monotonic) >= idle_limit:
                raise TimeoutError(f"no new vibration frame for {idle_limit:.1f}s")

            if frame is not None:
                emitted += 1
                yield frame

            next_t += period_s

    def __enter__(self) -> "RealtimeVibrationReader":
        if not self.connect():
            raise RuntimeError(
                f"connect failed port={self.port} baud={self.baud} addr={hex(self.addr)}"
            )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="VB01 实时振动数据读取")
    parser.add_argument("--elevator-id", default=env_str("MONITOR_ELEVATOR_ID", "elevator-unknown"), help="电梯唯一ID")
    parser.add_argument("--device-name", default=env_str("MONITOR_DEVICE_NAME", DEFAULT_DEVICE_NAME), help="设备名称")
    parser.add_argument("--port", default=env_str("MONITOR_PORT", "/dev/ttyUSB0"), help="串口")
    parser.add_argument("--baud", type=int, default=env_int("MONITOR_BAUD", 115200), help="波特率")
    parser.add_argument("--addr", type=_parse_int_auto, default=env_int("MONITOR_ADDR", 0x50), help="设备地址(支持 0x 前缀)")
    parser.add_argument("--sample-hz", type=float, default=env_float("MONITOR_SAMPLE_HZ", 100.0), help="采样频率")
    parser.add_argument("--detect-hz", type=int, default=env_int("MONITOR_DETECT_HZ", 100), help="设备检测周期(寄存器 0x65)")
    parser.add_argument("--no-set-detect-hz", action="store_true", help="不写设备检测周期")
    parser.add_argument("--startup-timeout-s", type=float, default=env_float("MONITOR_STARTUP_TIMEOUT_S", 3.0), help="连接后等待首帧超时")
    parser.add_argument("--max-data-age-ms", type=int, default=env_int("MONITOR_MAX_DATA_AGE_MS", 500), help="可接受数据最大延迟")
    parser.add_argument("--reg-addr", type=_parse_int_auto, default=0x34, help="循环读取起始寄存器")
    parser.add_argument("--reg-count", type=int, default=19, help="循环读取寄存器数量")
    parser.add_argument("--duration-s", type=float, default=None, help="读取持续秒数")
    parser.add_argument("--limit", type=int, default=None, help="最多读取多少帧")
    parser.add_argument("--poll-s", type=float, default=0.01, help="无新帧时轮询间隔")
    parser.add_argument("--max-idle-s", type=float, default=env_float("MONITOR_RECONNECT_NO_DATA_S", 8.0), help="连续无新帧超过该秒数则退出")
    parser.add_argument("--emit-mode", choices=["new", "fixed"], default="new", help="new=仅写新帧；fixed=按固定频率输出")
    parser.add_argument("--emit-hz", type=float, default=100.0, help="fixed 模式输出频率")
    parser.add_argument("--format", choices=["jsonl", "csv"], default="jsonl", help="stdout 输出格式")
    parser.add_argument("--output-csv", default="", help="可选：额外保存到 CSV 文件")
    return parser


def _to_csv_line(frame: dict[str, Any]) -> str:
    values = [frame.get(field, "") for field in VIBRATION_FIELDS]
    buff = []
    for value in values:
        if value is None:
            buff.append("")
        else:
            buff.append(str(value))
    return ",".join(buff)


def main() -> int:
    args = build_arg_parser().parse_args()

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
            f"connect failed port={args.port} baud={args.baud} addr={hex(args.addr)}",
            file=sys.stderr,
        )
        return 1

    if args.startup_timeout_s > 0 and reader.device is not None:
        got_first = reader.device.wait_for_data(timeout_s=max(0.0, float(args.startup_timeout_s)))
        if not got_first:
            print(
                f"startup timeout: no first frame within {float(args.startup_timeout_s):.2f}s",
                file=sys.stderr,
            )
            reader.close()
            return 2

    recorder: Optional[DataRecorder] = None
    if args.output_csv:
        recorder = DataRecorder(
            args.output_csv,
            file_format="csv",
            fieldnames=VIBRATION_FIELDS,
            flush=True,
        ).open()

    emitted = 0
    exit_code = 0
    try:
        if args.format == "csv":
            print(",".join(VIBRATION_FIELDS))

        if args.emit_mode == "fixed":
            frame_iter = reader.iter_frames_fixed_rate(
                emit_hz=args.emit_hz,
                duration_s=args.duration_s,
                limit=args.limit,
                max_idle_s=args.max_idle_s,
            )
        else:
            frame_iter = reader.iter_frames(
                duration_s=args.duration_s,
                limit=args.limit,
                poll_s=args.poll_s,
                max_idle_s=args.max_idle_s,
            )

        for frame in frame_iter:
            emitted += 1
            if "is_new_frame" not in frame:
                frame["is_new_frame"] = 1
            if args.format == "jsonl":
                print(json.dumps(frame, ensure_ascii=False))
            else:
                print(_to_csv_line(frame))
            if recorder is not None:
                recorder.write(frame)
    except KeyboardInterrupt:
        print("stopped by user", file=sys.stderr)
        exit_code = 130
    except TimeoutError as ex:
        print(str(ex), file=sys.stderr)
        exit_code = 2
    finally:
        if recorder is not None:
            recorder.close()
        reader.close()

    if exit_code != 0:
        return exit_code
    if emitted == 0:
        print(
            "no realtime vibration frame captured; check port/addr or increase --max-data-age-ms",
            file=sys.stderr,
        )
        return 2
    return 0


__all__ = [
    "RealtimeVibrationReader",
    "VIBRATION_FIELDS",
    "build_arg_parser",
    "build_vibration_frame",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
