from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from elevator_monitor.common import parse_float, vector_magnitude


VIBRATION_CSV_FIELDS = (
    "elevator_id",
    "ts_ms",
    "ts",
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
    "A_mag",
    "G_mag",
)


def _format_ts_ms(ts_ms: int) -> str:
    dt = datetime.fromtimestamp(ts_ms / 1000.0)
    return dt.strftime("%Y-%m-%d %H:%M:%S.") + f"{ts_ms % 1000:03d}"


def _require_float(payload: Mapping[str, Any], key: str) -> float:
    value = parse_float(payload.get(key))
    if value is None:
        raise ValueError(f"missing required vibration field: {key}")
    return value


def _optional_float(payload: Mapping[str, Any], *keys: str, default: float = 0.0) -> float:
    for key in keys:
        value = parse_float(payload.get(key))
        if value is not None:
            return value
    return default


def legacy_dtu_row_to_vibration_row(
    row: Mapping[str, Any],
    *,
    elevator_id: str = "elevator-001",
) -> dict[str, str]:
    ts_ms = int(str(row.get("ts", "")).strip())
    payload = json.loads(str(row.get("dtu_vib", "")).strip())

    ax = _require_float(payload, "AX")
    ay = _require_float(payload, "AY")
    az = _require_float(payload, "AZ")
    gx = _require_float(payload, "GX")
    gy = _require_float(payload, "GY")
    gz = _require_float(payload, "GZ")
    vx = _optional_float(payload, "VX")
    vy = _optional_float(payload, "VY")
    vz = _optional_float(payload, "VZ")
    ang_x = _optional_float(payload, "YAW")
    ang_y = _optional_float(payload, "ROLL")
    ang_z = _optional_float(payload, "PITCH")
    temp = _optional_float(payload, "TEMPB", "TEMP")
    sx = int(round(_optional_float(payload, "DX")))
    sy = int(round(_optional_float(payload, "DY")))
    sz = int(round(_optional_float(payload, "DZ")))
    fx = int(round(_optional_float(payload, "HZX") * 10.0))
    fy = int(round(_optional_float(payload, "HZY") * 10.0))
    fz = int(round(_optional_float(payload, "HZZ") * 10.0))
    a_mag = vector_magnitude(ax, ay, az)
    g_mag = vector_magnitude(gx, gy, gz)

    return {
        "elevator_id": elevator_id,
        "ts_ms": str(ts_ms),
        "ts": _format_ts_ms(ts_ms),
        "data_ts_ms": str(ts_ms),
        "data_age_ms": "0",
        "is_new_frame": "1",
        "Ax": str(ax),
        "Ay": str(ay),
        "Az": str(az),
        "Gx": str(gx),
        "Gy": str(gy),
        "Gz": str(gz),
        "vx": str(vx),
        "vy": str(vy),
        "vz": str(vz),
        "ax": str(ang_x),
        "ay": str(ang_y),
        "az": str(ang_z),
        "t": str(temp),
        "sx": str(sx),
        "sy": str(sy),
        "sz": str(sz),
        "fx": str(fx),
        "fy": str(fy),
        "fz": str(fz),
        "A_mag": str(a_mag),
        "G_mag": str(g_mag),
    }


def convert_legacy_dtu_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    elevator_id: str = "elevator-001",
) -> list[dict[str, str]]:
    converted = [legacy_dtu_row_to_vibration_row(row, elevator_id=elevator_id) for row in rows]
    converted.sort(key=lambda row: int(row["ts_ms"]))
    return converted


def convert_legacy_dtu_csv_file(
    input_path: Path,
    *,
    output_path: Path | None = None,
    elevator_id: str = "elevator-001",
) -> list[dict[str, str]]:
    with input_path.open("r", encoding="utf-8", newline="") as fp:
        rows = convert_legacy_dtu_rows(csv.DictReader(fp), elevator_id=elevator_id)

    target_path = output_path or input_path
    with target_path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(VIBRATION_CSV_FIELDS))
        writer.writeheader()
        writer.writerows(rows)

    return rows
