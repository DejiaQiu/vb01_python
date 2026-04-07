from __future__ import annotations

import csv
import gzip
import io
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


def _normalize_record_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            normalized.append(dict(row))
    return normalized


def _load_csv_text(text: str) -> list[dict[str, Any]]:
    return [dict(row) for row in csv.DictReader(io.StringIO(text))]


def _load_jsonl_text(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            records.append(payload)
    return records


def _infer_file_format(path: Path) -> str:
    suffixes = [suffix.lower() for suffix in path.suffixes]
    if suffixes[-2:] in ([".jsonl", ".gz"], [".ndjson", ".gz"]):
        return "jsonl"
    if suffixes[-2:] == [".csv", ".gz"]:
        return "csv"
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return "csv"
    if suffix in {".jsonl", ".ndjson"}:
        return "jsonl"
    raise ValueError(f"Unsupported file format: {path.suffix} (supports .csv/.csv.gz/.jsonl/.jsonl.gz/.ndjson/.ndjson.gz)")


def load_records(path: Path) -> list[dict[str, Any]]:
    file_format = _infer_file_format(path)
    raw = path.read_bytes()
    if path.suffix.lower() == ".gz":
        raw = gzip.decompress(raw)
    text = raw.decode("utf-8", errors="replace")
    if file_format == "csv":
        return _normalize_record_rows(_load_csv_text(text))
    if file_format == "jsonl":
        return _normalize_record_rows(_load_jsonl_text(text))
    raise ValueError(f"Unsupported file format: {path.suffix}")


def dumps_jsonl(rows: list[dict[str, Any]]) -> str:
    lines = [json.dumps(row, ensure_ascii=False) for row in _normalize_record_rows(rows)]
    return "\n".join(lines) + ("\n" if lines else "")


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
