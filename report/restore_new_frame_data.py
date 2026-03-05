#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional


def parse_float(value: object) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_int(value: object) -> Optional[int]:
    f = parse_float(value)
    if f is None:
        return None
    return int(round(f))


def clamp_i16(value: float) -> int:
    iv = int(round(value))
    if iv < -32768:
        return -32768
    if iv > 32767:
        return 32767
    return iv


def i16_to_u16(value: int) -> int:
    return value & 0xFFFF


def format_ts_ms(ts_ms: int) -> str:
    dt = datetime.fromtimestamp(ts_ms / 1000.0)
    return dt.strftime("%Y-%m-%d %H:%M:%S.") + f"{ts_ms % 1000:03d}"


RAW_RESTORE_RULES: dict[str, Callable[[float], float]] = {
    # reg 0x34~0x36: accel = raw / 32768 * 16
    "Ax": lambda x: x / 16.0 * 32768.0,
    "Ay": lambda x: x / 16.0 * 32768.0,
    "Az": lambda x: x / 16.0 * 32768.0,
    # reg 0x37~0x39: gyro = raw / 32768 * 2000
    "Gx": lambda x: x / 2000.0 * 32768.0,
    "Gy": lambda x: x / 2000.0 * 32768.0,
    "Gz": lambda x: x / 2000.0 * 32768.0,
    # reg 0x3A~0x3C: direct int
    "vx": lambda x: x,
    "vy": lambda x: x,
    "vz": lambda x: x,
    # reg 0x3D~0x3F: angle = raw / 32768 * 180
    "ax": lambda x: x / 180.0 * 32768.0,
    "ay": lambda x: x / 180.0 * 32768.0,
    "az": lambda x: x / 180.0 * 32768.0,
    # reg 0x40: temp = raw / 100
    "t": lambda x: x * 100.0,
    # reg 0x41~0x46: direct int
    "sx": lambda x: x,
    "sy": lambda x: x,
    "sz": lambda x: x,
    "fx": lambda x: x,
    "fy": lambda x: x,
    "fz": lambda x: x,
}


def add_raw_columns(rows: list[dict[str, str]]) -> list[str]:
    added_fields: list[str] = []
    for field_name, recover_fn in RAW_RESTORE_RULES.items():
        raw_i16_key = f"{field_name}_raw_i16"
        raw_u16_key = f"{field_name}_raw_u16"
        added_fields.extend([raw_i16_key, raw_u16_key])

        for row in rows:
            src_val = parse_float(row.get(field_name))
            if src_val is None:
                row[raw_i16_key] = ""
                row[raw_u16_key] = ""
                continue

            restored_i16 = clamp_i16(recover_fn(src_val))
            row[raw_i16_key] = str(restored_i16)
            row[raw_u16_key] = str(i16_to_u16(restored_i16))
    return added_fields


def rebuild_fixed_rate(rows: list[dict[str, str]], fixed_hz: float) -> list[dict[str, str]]:
    if fixed_hz <= 0:
        raise ValueError("fixed_hz must be > 0")
    if len(rows) <= 1:
        return rows

    period_ms = 1000.0 / float(fixed_hz)

    indexed: list[tuple[int, dict[str, str], int]] = []
    for row in rows:
        ts_ms = parse_int(row.get("ts_ms"))
        if ts_ms is None:
            continue
        data_ts_ms = parse_int(row.get("data_ts_ms"))
        if data_ts_ms is None:
            data_ts_ms = ts_ms
        indexed.append((ts_ms, row, data_ts_ms))

    if not indexed:
        return rows

    indexed.sort(key=lambda x: x[0])
    start_ts = indexed[0][0]
    end_ts = indexed[-1][0]

    out: list[dict[str, str]] = []
    src_idx = 0
    prev_src_idx = -1
    step_count = int((end_ts - start_ts) / period_ms) + 1

    for i in range(step_count):
        cur_ts = int(round(start_ts + i * period_ms))
        while src_idx + 1 < len(indexed) and indexed[src_idx + 1][2] <= cur_ts:
            src_idx += 1

        _, src_row, src_data_ts = indexed[src_idx]
        row = dict(src_row)
        row["ts_ms"] = str(cur_ts)
        row["ts"] = format_ts_ms(cur_ts)
        row["data_ts_ms"] = str(src_data_ts)
        row["data_age_ms"] = str(cur_ts - src_data_ts)
        row["is_new_frame"] = "1" if src_idx != prev_src_idx else "0"
        row["restored_fixed_rate"] = "1"
        out.append(row)
        prev_src_idx = src_idx

    return out


def to_legacy_dtu_row(row: dict[str, str], dtu_id: str) -> dict[str, str]:
    def g(name: str, default: float = 0.0) -> float:
        v = parse_float(row.get(name))
        return float(default if v is None else v)

    ts_ms = parse_int(row.get("ts_ms"))
    if ts_ms is None:
        ts_ms = 0

    vib_payload = {
        "AX": g("Ax"),
        "AY": g("Ay"),
        "AZ": g("Az"),
        "DX": int(round(g("sx"))),
        "DY": int(round(g("sy"))),
        "DZ": int(round(g("sz"))),
        "GX": g("Gx"),
        "GY": g("Gy"),
        "GZ": g("Gz"),
        "VX": g("vx"),
        "VY": g("vy"),
        "VZ": g("vz"),
        "HZX": g("fx") / 10.0,
        "HZY": g("fy") / 10.0,
        "HZZ": g("fz") / 10.0,
        "YAW": g("ax"),
        "ROLL": g("ay"),
        "PITCH": g("az"),
        "TEMPB": g("t"),
    }

    return {
        "dtu_id": dtu_id,
        "ts": str(ts_ms),
        "dtu_data": "{}",
        "dtu_vib": json.dumps(vib_payload, ensure_ascii=False),
    }


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="把 new 帧模式 CSV 还原为可复盘的原始寄存器数据（并可导出 legacy dtu_vib 格式）"
    )
    parser.add_argument("--input", required=True, help="输入 CSV（new 帧模式）")
    parser.add_argument("--output", required=True, help="输出 CSV（带 *_raw_i16/*_raw_u16）")
    parser.add_argument("--fixed-hz", type=float, default=0.0, help="可选：补齐固定频率（例如 100）")
    parser.add_argument("--legacy-dtu-output", default="", help="可选：导出旧 dtu 格式 CSV")
    parser.add_argument("--dtu-id", default="elevator-001", help="legacy dtu 输出使用的 dtu_id")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()

    with input_path.open("r", encoding="utf-8", newline="") as fp:
        reader = csv.DictReader(fp)
        src_fields = list(reader.fieldnames or [])
        rows = list(reader)

    if not rows:
        raise SystemExit(f"empty input file: {input_path}")

    rows.sort(key=lambda r: parse_int(r.get("ts_ms")) or 0)

    if args.fixed_hz and float(args.fixed_hz) > 0:
        rows = rebuild_fixed_rate(rows, float(args.fixed_hz))
        if "restored_fixed_rate" not in src_fields:
            src_fields.append("restored_fixed_rate")

    added_raw_fields = add_raw_columns(rows)
    out_fields = src_fields + [f for f in added_raw_fields if f not in src_fields]
    write_csv(output_path, rows, out_fields)

    print(f"restored rows={len(rows)}")
    print(f"raw output={output_path}")

    if args.legacy_dtu_output:
        dtu_rows = [to_legacy_dtu_row(row, dtu_id=args.dtu_id) for row in rows]
        legacy_path = Path(args.legacy_dtu_output).resolve()
        write_csv(legacy_path, dtu_rows, ["dtu_id", "ts", "dtu_data", "dtu_vib"])
        print(f"legacy dtu output={legacy_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
