from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Optional


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

CORE_FIELDS = ("Ax", "Ay", "Az", "Gx", "Gy", "Gz", "t")
FEATURE_FIELDS = ("A_mag", "G_mag", "T", "V_mag", "ANG_mag", "S_mag", "F_mag")


def parse_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s or s.lower() == "none":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def load_records(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8", newline="") as fp:
            return list(csv.DictReader(fp))
    if suffix in {".jsonl", ".ndjson"}:
        records: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                records.append(json.loads(line))
        return records
    raise ValueError(f"Unsupported file format: {path.suffix} (supports .csv/.jsonl/.ndjson)")


def vector_magnitude(x: Optional[float], y: Optional[float], z: Optional[float]) -> Optional[float]:
    if x is None or y is None or z is None:
        return None
    return (x * x + y * y + z * z) ** 0.5


def extract_features(row: dict[str, Any]) -> dict[str, Optional[float]]:
    ax = parse_float(row.get("Ax"))
    ay = parse_float(row.get("Ay"))
    az = parse_float(row.get("Az"))

    gx = parse_float(row.get("Gx"))
    gy = parse_float(row.get("Gy"))
    gz = parse_float(row.get("Gz"))

    vx = parse_float(row.get("vx"))
    vy = parse_float(row.get("vy"))
    vz = parse_float(row.get("vz"))

    angx = parse_float(row.get("ax"))
    angy = parse_float(row.get("ay"))
    angz = parse_float(row.get("az"))

    sx = parse_float(row.get("sx"))
    sy = parse_float(row.get("sy"))
    sz = parse_float(row.get("sz"))

    fx = parse_float(row.get("fx"))
    fy = parse_float(row.get("fy"))
    fz = parse_float(row.get("fz"))

    t = parse_float(row.get("t"))

    return {
        "A_mag": vector_magnitude(ax, ay, az),
        "G_mag": vector_magnitude(gx, gy, gz),
        "T": t,
        "V_mag": vector_magnitude(vx, vy, vz),
        "ANG_mag": vector_magnitude(angx, angy, angz),
        "S_mag": vector_magnitude(sx, sy, sz),
        "F_mag": vector_magnitude(fx, fy, fz),
    }


def missing_ratio(row: dict[str, Any], fields: tuple[str, ...] = CORE_FIELDS) -> float:
    missing = 0
    for key in fields:
        if parse_float(row.get(key)) is None:
            missing += 1
    return missing / len(fields)


def core_signature(row: dict[str, Any], fields: tuple[str, ...] = CORE_FIELDS) -> Optional[tuple[str, ...]]:
    vals = tuple(str(row.get(k, "")).strip() for k in fields)
    if all(v == "" or v.lower() == "none" for v in vals):
        return None
    return vals
