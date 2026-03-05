from __future__ import annotations

import statistics
from typing import Any

from ..common import core_signature, extract_features, missing_ratio, parse_float


WINDOW_FEATURE_FIELDS = (
    "A_mag_mean",
    "A_mag_std",
    "A_mag_max",
    "A_mag_p90",
    "A_mag_energy",
    "A_mag_delta",
    "G_mag_mean",
    "G_mag_std",
    "G_mag_max",
    "G_mag_p90",
    "G_mag_energy",
    "G_mag_delta",
    "T_mean",
    "T_std",
    "T_max",
    "T_delta",
    "Ax_std",
    "Ay_std",
    "Az_std",
    "Gx_std",
    "Gy_std",
    "Gz_std",
    "missing_ratio_mean",
    "missing_ratio_max",
    "stale_ratio",
    "sample_count",
    "duration_s",
)


def _safe_std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    return statistics.pstdev(values)


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    idx = int((len(sorted_values) - 1) * q)
    return float(sorted_values[idx])


def _series_stats(values: list[float], prefix: str) -> dict[str, float]:
    if not values:
        return {
            f"{prefix}_mean": 0.0,
            f"{prefix}_std": 0.0,
            f"{prefix}_max": 0.0,
            f"{prefix}_p90": 0.0,
            f"{prefix}_energy": 0.0,
            f"{prefix}_delta": 0.0,
        }

    return {
        f"{prefix}_mean": float(statistics.fmean(values)),
        f"{prefix}_std": float(_safe_std(values)),
        f"{prefix}_max": float(max(values)),
        f"{prefix}_p90": float(_percentile(values, 0.9)),
        f"{prefix}_energy": float(statistics.fmean(v * v for v in values)),
        f"{prefix}_delta": float(values[-1] - values[0]),
    }


def _axis_std(rows: list[dict[str, Any]], field: str) -> float:
    values = [v for v in (parse_float(row.get(field)) for row in rows) if v is not None]
    return float(_safe_std(values)) if values else 0.0


def _duration_s(rows: list[dict[str, Any]]) -> float:
    if len(rows) < 2:
        return 0.0

    ts_values: list[int] = []
    for row in rows:
        raw = row.get("ts_ms")
        if raw is None:
            continue
        try:
            ts_values.append(int(float(raw)))
        except (TypeError, ValueError):
            continue

    if len(ts_values) < 2:
        return 0.0
    return max(0.0, (max(ts_values) - min(ts_values)) / 1000.0)


def _stale_ratio(rows: list[dict[str, Any]]) -> float:
    prev_sig = None
    comparable_count = 0
    stale_count = 0

    for row in rows:
        sig = core_signature(row)
        if sig is None:
            continue
        if prev_sig is not None:
            comparable_count += 1
            if sig == prev_sig:
                stale_count += 1
        prev_sig = sig

    if comparable_count <= 0:
        return 0.0
    return stale_count / comparable_count


def extract_window_features(rows: list[dict[str, Any]]) -> dict[str, float]:
    if not rows:
        return {name: 0.0 for name in WINDOW_FEATURE_FIELDS}

    a_mag_values: list[float] = []
    g_mag_values: list[float] = []
    temp_values: list[float] = []
    miss_values: list[float] = []

    for row in rows:
        feats = extract_features(row)
        a_mag = feats.get("A_mag")
        g_mag = feats.get("G_mag")
        temp = feats.get("T")

        if a_mag is not None:
            a_mag_values.append(float(a_mag))
        if g_mag is not None:
            g_mag_values.append(float(g_mag))
        if temp is not None:
            temp_values.append(float(temp))

        miss_values.append(float(missing_ratio(row)))

    out: dict[str, float] = {}
    out.update(_series_stats(a_mag_values, "A_mag"))
    out.update(_series_stats(g_mag_values, "G_mag"))

    if temp_values:
        out["T_mean"] = float(statistics.fmean(temp_values))
        out["T_std"] = float(_safe_std(temp_values))
        out["T_max"] = float(max(temp_values))
        out["T_delta"] = float(temp_values[-1] - temp_values[0])
    else:
        out["T_mean"] = 0.0
        out["T_std"] = 0.0
        out["T_max"] = 0.0
        out["T_delta"] = 0.0

    out["Ax_std"] = _axis_std(rows, "Ax")
    out["Ay_std"] = _axis_std(rows, "Ay")
    out["Az_std"] = _axis_std(rows, "Az")
    out["Gx_std"] = _axis_std(rows, "Gx")
    out["Gy_std"] = _axis_std(rows, "Gy")
    out["Gz_std"] = _axis_std(rows, "Gz")

    out["missing_ratio_mean"] = float(statistics.fmean(miss_values)) if miss_values else 0.0
    out["missing_ratio_max"] = float(max(miss_values)) if miss_values else 0.0
    out["stale_ratio"] = float(_stale_ratio(rows))
    out["sample_count"] = float(len(rows))
    out["duration_s"] = float(_duration_s(rows))

    # Keep a stable schema for downstream training/inference.
    return {name: float(out.get(name, 0.0)) for name in WINDOW_FEATURE_FIELDS}
