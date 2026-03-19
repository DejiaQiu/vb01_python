"""钢丝绳状态异常单窗诊断。

当前实现不再把目标限定为“低频横摆型松绳”，而是统一输出
`rope_tension_abnormal`，并在内部区分两类更常见的振动画像：

1. loose_like
   更偏横向摆动、低频能量和加角耦合增强。
2. tight_like
   更偏竖向传递、jerk / 角速度增强和谱峰集中。

如果仓库里存在 centroid 二分类模型，则最终分数会融合模型概率；
否则退回规则分数，保证离线/API 不会因为模型文件缺失而直接失效。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from elevator_monitor.training.centroid_model import CentroidModel, fit_centroid_classifier
except ImportError:  # pragma: no cover
    CentroidModel = None  # type: ignore[assignment]
    fit_centroid_classifier = None  # type: ignore[assignment]

try:
    from ._base import (
        build_clean_feature_baseline,
        build_feature_pack,
        build_result,
        clamp,
        load_rows,
        parse_float,
        ratio_to_100,
    )
except ImportError:  # pragma: no cover
    from _base import build_clean_feature_baseline, build_feature_pack, build_result, clamp, load_rows, parse_float, ratio_to_100


FAULT_TYPE = "rope_tension_abnormal"
DEFAULT_MODEL_PATH = Path("data/models/rope_tension_abnormal_centroid.json")
MODEL_ENV_KEY = "ROPE_TENSION_MODEL_JSON"

ROPE_BASELINE_KEYS = (
    "a_rms_ac",
    "a_p2p",
    "g_std",
    "zc_rate_hz",
    "lateral_ratio",
    "ag_corr",
    "gx_ax_corr",
    "gy_ay_corr",
    "peak_rate_hz",
    "a_crest",
    "a_kurt",
    "energy_z_over_xy",
    "az_p2p",
    "az_cv",
    "az_jerk_rms",
    "corr_xz",
    "corr_yz",
    "lat_dom_freq_hz",
    "lat_peak_ratio",
    "lat_low_band_ratio",
    "z_dom_freq_hz",
    "z_peak_ratio",
    "z_low_band_ratio",
)

ROPE_MODEL_FIELDS = (
    "lateral_ratio_rel",
    "energy_z_over_xy_rel",
    "a_rms_ac_rel",
    "a_p2p_rel",
    "g_std_rel",
    "az_p2p_rel",
    "az_cv_rel",
    "az_jerk_rms_rel",
    "ag_corr_rel",
    "corr_xz_shift",
    "corr_yz_shift",
    "lat_dom_freq_low",
    "lat_peak_ratio_rel",
    "lat_low_band_ratio_rel",
    "z_dom_freq_shift",
    "z_peak_ratio_rel",
    "z_low_band_ratio_rel",
)

MIN_SCORE_WATCH = 45.0
MIN_SCORE_TRIGGER = 60.0

EPS = 1e-9
_MODEL_CACHE: dict[str, tuple[float, Optional["CentroidModel"]]] = {}


def _to_float(value: object, default: float = 0.0) -> float:
    parsed = parse_float(value)
    return float(parsed if parsed is not None else default)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _baseline_stats(features: dict[str, Any]) -> dict[str, tuple[float, float]]:
    payload = features.get("baseline")
    if not isinstance(payload, dict):
        return {}
    raw_stats = payload.get("stats") if isinstance(payload.get("stats"), dict) else payload

    stats: dict[str, tuple[float, float]] = {}
    for key, item in raw_stats.items():
        if not isinstance(item, dict):
            continue
        med = parse_float(item.get("median"))
        scale = parse_float(item.get("scale"))
        if med is None or scale is None or scale <= EPS:
            continue
        stats[str(key)] = (float(med), float(max(scale, 1e-6)))
    return stats


def _positive_z(value: float, stat: tuple[float, float] | None) -> float:
    if stat is None:
        return 0.0
    med, scale = stat
    return max(0.0, (float(value) - med) / max(scale, 1e-6))


def _negative_z(value: float, stat: tuple[float, float] | None) -> float:
    if stat is None:
        return 0.0
    med, scale = stat
    return max(0.0, (med - float(value)) / max(scale, 1e-6))


def _abs_shift_z(value: float, stat: tuple[float, float] | None) -> float:
    if stat is None:
        return 0.0
    med, scale = stat
    return abs(float(value) - med) / max(scale, 1e-6)


def _z_to_100(z_value: float, softness: float = 2.0) -> float:
    z_pos = max(0.0, float(z_value))
    if z_pos <= 0.0:
        return 0.0
    return 100.0 * (1.0 - math.exp(-z_pos / max(0.35, float(softness))))


def _normalize_weight(count: int, total: int) -> float:
    if total <= 0:
        return 0.0
    coverage = max(0.0, min(1.0, float(count) / float(total)))
    return 0.85 * coverage


def _score_to_level(score: float) -> str:
    if score >= 80.0:
        return "alarm"
    if score >= 60.0:
        return "warning"
    if score >= 45.0:
        return "watch"
    return "normal"


def _branch_fallback_components(features: dict[str, Any]) -> dict[str, float]:
    a_mean = max(abs(_to_float(features.get("a_mean"), 1.0)), 1e-3)
    g_mean = max(abs(_to_float(features.get("g_mean"), 0.3)), 0.05)
    az_rms_ac = max(abs(_to_float(features.get("az_rms_ac"), 0.01)), 1e-4)
    lateral_ratio = _to_float(features.get("lateral_ratio"), 1.0)
    zc_rate_hz = _to_float(features.get("zc_rate_hz"))
    a_p2p = _to_float(features.get("a_p2p"))
    g_std = _to_float(features.get("g_std"))
    peak_rate_hz = _to_float(features.get("peak_rate_hz"))
    a_crest = _to_float(features.get("a_crest"))
    a_kurt = _to_float(features.get("a_kurt"))
    energy_z_over_xy = _to_float(features.get("energy_z_over_xy"), 1.0)
    az_p2p = _to_float(features.get("az_p2p"))
    az_cv = _to_float(features.get("az_cv"))
    az_jerk_rms = _to_float(features.get("az_jerk_rms"))
    ag_corr = _to_float(features.get("ag_corr"))
    gx_ax_corr = _to_float(features.get("gx_ax_corr"))
    gy_ay_corr = _to_float(features.get("gy_ay_corr"))
    corr_xz = _to_float(features.get("corr_xz"))
    corr_yz = _to_float(features.get("corr_yz"))
    lat_dom_freq_hz = _to_float(features.get("lat_dom_freq_hz"))
    lat_peak_ratio = _to_float(features.get("lat_peak_ratio"))
    lat_low_band_ratio = _to_float(features.get("lat_low_band_ratio"))
    z_dom_freq_hz = _to_float(features.get("z_dom_freq_hz"))
    z_peak_ratio = _to_float(features.get("z_peak_ratio"))
    z_low_band_ratio = _to_float(features.get("z_low_band_ratio"))

    corr_major = max(ag_corr, gx_ax_corr, gy_ay_corr)
    loose_lateral = (
        0.55 * ratio_to_100(lateral_ratio, 1.05, 1.75)
        + 0.45 * ratio_to_100(lat_low_band_ratio, 0.22, 0.70)
    )
    loose_lowfreq = (
        0.70 * (100.0 - ratio_to_100(lat_dom_freq_hz, 1.40, 4.80))
        + 0.30 * (100.0 - ratio_to_100(zc_rate_hz, 0.80, 6.00))
    )
    loose_coupling = (
        0.65 * ratio_to_100(corr_major, 0.10, 0.60)
        + 0.35 * ratio_to_100(max(abs(corr_xz), abs(corr_yz)), 0.06, 0.40)
    )
    loose_energy = (
        0.55 * ratio_to_100(a_p2p / max(a_mean, EPS), 0.020, 0.180)
        + 0.45 * ratio_to_100(g_std / max(g_mean, EPS), 0.020, 0.260)
    )

    tight_vertical = (
        0.45 * ratio_to_100(energy_z_over_xy, 0.95, 1.85)
        + 0.30 * ratio_to_100(az_p2p, 0.10, 0.28)
        + 0.25 * ratio_to_100(az_cv, 0.45, 1.30)
    )
    tight_dynamic = (
        0.55 * ratio_to_100(az_jerk_rms / max(az_rms_ac, EPS), 0.85, 2.50)
        + 0.45 * ratio_to_100(g_std / max(g_mean, EPS), 0.020, 0.260)
    )
    tight_spectral = (
        0.50 * ratio_to_100(z_peak_ratio, 0.16, 0.58)
        + 0.25 * ratio_to_100(z_low_band_ratio, 0.18, 0.68)
        + 0.25 * ratio_to_100(lat_peak_ratio, 0.16, 0.52)
    )

    spiky_penalty = (
        0.45 * ratio_to_100(a_crest, 1.8, 4.2)
        + 0.35 * ratio_to_100(a_kurt, 1.0, 6.0)
        + 0.20 * ratio_to_100(peak_rate_hz, 0.40, 4.00)
    )

    return {
        "loose_lateral": float(loose_lateral),
        "loose_lowfreq": float(loose_lowfreq),
        "loose_coupling": float(loose_coupling),
        "loose_energy": float(loose_energy),
        "tight_vertical": float(tight_vertical),
        "tight_dynamic": float(tight_dynamic),
        "tight_spectral": float(tight_spectral),
        "spiky_penalty": float(spiky_penalty),
        "corr_major": float(corr_major),
        "lat_dom_freq_hz": float(lat_dom_freq_hz),
        "lat_peak_ratio": float(lat_peak_ratio),
        "lat_low_band_ratio": float(lat_low_band_ratio),
        "z_dom_freq_hz": float(z_dom_freq_hz),
        "z_peak_ratio": float(z_peak_ratio),
        "z_low_band_ratio": float(z_low_band_ratio),
    }


def _branch_robust_components(features: dict[str, Any], baseline_stats: dict[str, tuple[float, float]]) -> dict[str, float]:
    lateral_ratio = _to_float(features.get("lateral_ratio"), 1.0)
    zc_rate_hz = _to_float(features.get("zc_rate_hz"))
    a_rms_ac = _to_float(features.get("a_rms_ac"))
    a_p2p = _to_float(features.get("a_p2p"))
    g_std = _to_float(features.get("g_std"))
    peak_rate_hz = _to_float(features.get("peak_rate_hz"))
    a_crest = _to_float(features.get("a_crest"))
    a_kurt = _to_float(features.get("a_kurt"))
    energy_z_over_xy = _to_float(features.get("energy_z_over_xy"), 1.0)
    az_p2p = _to_float(features.get("az_p2p"))
    az_cv = _to_float(features.get("az_cv"))
    az_jerk_rms = _to_float(features.get("az_jerk_rms"))
    ag_corr = _to_float(features.get("ag_corr"))
    gx_ax_corr = _to_float(features.get("gx_ax_corr"))
    gy_ay_corr = _to_float(features.get("gy_ay_corr"))
    corr_xz = _to_float(features.get("corr_xz"))
    corr_yz = _to_float(features.get("corr_yz"))
    lat_dom_freq_hz = _to_float(features.get("lat_dom_freq_hz"))
    lat_peak_ratio = _to_float(features.get("lat_peak_ratio"))
    lat_low_band_ratio = _to_float(features.get("lat_low_band_ratio"))
    z_dom_freq_hz = _to_float(features.get("z_dom_freq_hz"))
    z_peak_ratio = _to_float(features.get("z_peak_ratio"))
    z_low_band_ratio = _to_float(features.get("z_low_band_ratio"))

    loose_lateral = _z_to_100(
        0.60 * _positive_z(lateral_ratio, baseline_stats.get("lateral_ratio"))
        + 0.40 * _positive_z(lat_low_band_ratio, baseline_stats.get("lat_low_band_ratio"))
    )
    loose_lowfreq = _z_to_100(
        0.70 * _negative_z(lat_dom_freq_hz, baseline_stats.get("lat_dom_freq_hz"))
        + 0.30 * _negative_z(zc_rate_hz, baseline_stats.get("zc_rate_hz"))
    )
    loose_coupling = _z_to_100(
        max(
            _positive_z(ag_corr, baseline_stats.get("ag_corr")),
            _positive_z(gx_ax_corr, baseline_stats.get("gx_ax_corr")),
            _positive_z(gy_ay_corr, baseline_stats.get("gy_ay_corr")),
            0.75 * _abs_shift_z(corr_xz, baseline_stats.get("corr_xz")),
            0.75 * _abs_shift_z(corr_yz, baseline_stats.get("corr_yz")),
        )
    )
    loose_energy = _z_to_100(
        max(
            _positive_z(a_rms_ac, baseline_stats.get("a_rms_ac")),
            _positive_z(a_p2p, baseline_stats.get("a_p2p")),
            0.70 * _positive_z(g_std, baseline_stats.get("g_std")),
            0.50 * _positive_z(lat_peak_ratio, baseline_stats.get("lat_peak_ratio")),
        )
    )

    tight_vertical = _z_to_100(
        max(
            _positive_z(energy_z_over_xy, baseline_stats.get("energy_z_over_xy")),
            _positive_z(az_p2p, baseline_stats.get("az_p2p")),
            0.75 * _positive_z(az_cv, baseline_stats.get("az_cv")),
        )
    )
    tight_dynamic = _z_to_100(
        max(
            _positive_z(az_jerk_rms, baseline_stats.get("az_jerk_rms")),
            0.75 * _positive_z(g_std, baseline_stats.get("g_std")),
            0.45 * _positive_z(a_rms_ac, baseline_stats.get("a_rms_ac")),
        )
    )
    tight_spectral = _z_to_100(
        max(
            _positive_z(z_peak_ratio, baseline_stats.get("z_peak_ratio")),
            _positive_z(z_low_band_ratio, baseline_stats.get("z_low_band_ratio")),
            0.60 * _positive_z(lat_peak_ratio, baseline_stats.get("lat_peak_ratio")),
            0.55 * _abs_shift_z(z_dom_freq_hz, baseline_stats.get("z_dom_freq_hz")),
        )
    )
    spiky_penalty = _z_to_100(
        0.45 * _positive_z(a_crest, baseline_stats.get("a_crest"))
        + 0.35 * _positive_z(a_kurt, baseline_stats.get("a_kurt"))
        + 0.20 * _positive_z(peak_rate_hz, baseline_stats.get("peak_rate_hz"))
    )

    corr_major = max(ag_corr, gx_ax_corr, gy_ay_corr)
    return {
        "loose_lateral": float(loose_lateral),
        "loose_lowfreq": float(loose_lowfreq),
        "loose_coupling": float(loose_coupling),
        "loose_energy": float(loose_energy),
        "tight_vertical": float(tight_vertical),
        "tight_dynamic": float(tight_dynamic),
        "tight_spectral": float(tight_spectral),
        "spiky_penalty": float(spiky_penalty),
        "corr_major": float(corr_major),
        "lat_dom_freq_hz": float(lat_dom_freq_hz),
        "lat_peak_ratio": float(lat_peak_ratio),
        "lat_low_band_ratio": float(lat_low_band_ratio),
        "z_dom_freq_hz": float(z_dom_freq_hz),
        "z_peak_ratio": float(z_peak_ratio),
        "z_low_band_ratio": float(z_low_band_ratio),
    }


def _mix_components(
    baseline_weight: float,
    robust: dict[str, float],
    fallback: dict[str, float],
) -> dict[str, float]:
    out: dict[str, float] = {}
    for key in fallback.keys():
        out[key] = float(baseline_weight * robust.get(key, 0.0) + (1.0 - baseline_weight) * fallback.get(key, 0.0))
    return out


def _build_model_row(features: dict[str, Any], mixed: dict[str, float], baseline_stats: dict[str, tuple[float, float]]) -> dict[str, float]:
    a_rms_ac = _to_float(features.get("a_rms_ac"))
    a_p2p = _to_float(features.get("a_p2p"))
    g_std = _to_float(features.get("g_std"))
    energy_z_over_xy = _to_float(features.get("energy_z_over_xy"))
    az_p2p = _to_float(features.get("az_p2p"))
    az_cv = _to_float(features.get("az_cv"))
    az_jerk_rms = _to_float(features.get("az_jerk_rms"))
    ag_corr = _to_float(features.get("ag_corr"))
    corr_xz = _to_float(features.get("corr_xz"))
    corr_yz = _to_float(features.get("corr_yz"))
    lat_dom_freq_hz = _to_float(features.get("lat_dom_freq_hz"))
    z_dom_freq_hz = _to_float(features.get("z_dom_freq_hz"))
    lat_peak_ratio = _to_float(features.get("lat_peak_ratio"))
    lat_low_band_ratio = _to_float(features.get("lat_low_band_ratio"))
    z_peak_ratio = _to_float(features.get("z_peak_ratio"))
    z_low_band_ratio = _to_float(features.get("z_low_band_ratio"))

    return {
        "lateral_ratio_rel": float(mixed.get("loose_lateral", 0.0)),
        "energy_z_over_xy_rel": float(mixed.get("tight_vertical", 0.0)),
        "a_rms_ac_rel": float(_z_to_100(_positive_z(a_rms_ac, baseline_stats.get("a_rms_ac"))) if baseline_stats else ratio_to_100(a_rms_ac, 0.004, 0.025)),
        "a_p2p_rel": float(_z_to_100(_positive_z(a_p2p, baseline_stats.get("a_p2p"))) if baseline_stats else ratio_to_100(a_p2p, 0.08, 0.30)),
        "g_std_rel": float(_z_to_100(_positive_z(g_std, baseline_stats.get("g_std"))) if baseline_stats else ratio_to_100(g_std, 0.04, 0.30)),
        "az_p2p_rel": float(_z_to_100(_positive_z(az_p2p, baseline_stats.get("az_p2p"))) if baseline_stats else ratio_to_100(az_p2p, 0.08, 0.26)),
        "az_cv_rel": float(_z_to_100(_positive_z(az_cv, baseline_stats.get("az_cv"))) if baseline_stats else ratio_to_100(az_cv, 0.35, 1.30)),
        "az_jerk_rms_rel": float(_z_to_100(_positive_z(az_jerk_rms, baseline_stats.get("az_jerk_rms"))) if baseline_stats else ratio_to_100(az_jerk_rms, 0.25, 1.20)),
        "ag_corr_rel": float(_z_to_100(_positive_z(ag_corr, baseline_stats.get("ag_corr"))) if baseline_stats else ratio_to_100(ag_corr, 0.08, 0.60)),
        "corr_xz_shift": float(_z_to_100(_abs_shift_z(corr_xz, baseline_stats.get("corr_xz"))) if baseline_stats else ratio_to_100(abs(corr_xz), 0.05, 0.40)),
        "corr_yz_shift": float(_z_to_100(_abs_shift_z(corr_yz, baseline_stats.get("corr_yz"))) if baseline_stats else ratio_to_100(abs(corr_yz), 0.05, 0.40)),
        "lat_dom_freq_low": float(_z_to_100(_negative_z(lat_dom_freq_hz, baseline_stats.get("lat_dom_freq_hz"))) if baseline_stats else 100.0 - ratio_to_100(lat_dom_freq_hz, 1.40, 4.80)),
        "lat_peak_ratio_rel": float(_z_to_100(_positive_z(lat_peak_ratio, baseline_stats.get("lat_peak_ratio"))) if baseline_stats else ratio_to_100(lat_peak_ratio, 0.16, 0.55)),
        "lat_low_band_ratio_rel": float(_z_to_100(_positive_z(lat_low_band_ratio, baseline_stats.get("lat_low_band_ratio"))) if baseline_stats else ratio_to_100(lat_low_band_ratio, 0.20, 0.70)),
        "z_dom_freq_shift": float(_z_to_100(_abs_shift_z(z_dom_freq_hz, baseline_stats.get("z_dom_freq_hz"))) if baseline_stats else ratio_to_100(abs(z_dom_freq_hz - 2.5), 0.10, 3.00)),
        "z_peak_ratio_rel": float(_z_to_100(_positive_z(z_peak_ratio, baseline_stats.get("z_peak_ratio"))) if baseline_stats else ratio_to_100(z_peak_ratio, 0.16, 0.55)),
        "z_low_band_ratio_rel": float(_z_to_100(_positive_z(z_low_band_ratio, baseline_stats.get("z_low_band_ratio"))) if baseline_stats else ratio_to_100(z_low_band_ratio, 0.18, 0.68)),
    }


def _resolve_model_path(features: dict[str, Any]) -> Optional[Path]:
    explicit = str(features.get("rope_model_path", "")).strip()
    if explicit:
        path = Path(explicit).expanduser().resolve()
        return path if path.exists() else None
    env_path = str(os.getenv(MODEL_ENV_KEY, "")).strip()
    if env_path:
        path = Path(env_path).expanduser().resolve()
        return path if path.exists() else None
    path = DEFAULT_MODEL_PATH.expanduser().resolve()
    return path if path.exists() else None


def _load_model(features: dict[str, Any]) -> Optional["CentroidModel"]:
    if bool(features.get("rope_disable_model", False)):
        return None
    model_obj = features.get("rope_model")
    if CentroidModel is None:
        return None
    if isinstance(model_obj, CentroidModel):
        return model_obj
    if isinstance(model_obj, dict):
        return CentroidModel.from_dict(model_obj)

    path = _resolve_model_path(features)
    if path is None:
        return None

    cache_key = str(path)
    mtime = path.stat().st_mtime
    cached = _MODEL_CACHE.get(cache_key)
    if cached is not None and abs(cached[0] - mtime) < 1e-9:
        return cached[1]

    model = CentroidModel.load(str(path))
    _MODEL_CACHE[cache_key] = (mtime, model)
    return model


def _analyze_rope_signature(features: dict[str, Any]) -> dict[str, Any]:
    a_mean = max(abs(_to_float(features.get("a_mean"), 1.0)), 1e-3)
    g_mean = max(abs(_to_float(features.get("g_mean"), 0.3)), 0.05)
    a_rms_ac = _to_float(features.get("a_rms_ac"))
    g_std = _to_float(features.get("g_std"))
    peak_rate_hz = _to_float(features.get("peak_rate_hz"))
    lat_peak_ratio = _to_float(features.get("lat_peak_ratio"))
    z_peak_ratio = _to_float(features.get("z_peak_ratio"))

    run_a_ratio = a_rms_ac / max(a_mean, EPS)
    run_g_ratio = g_std / max(g_mean, EPS)
    run_state_score = (
        0.45 * ratio_to_100(run_a_ratio, 0.0015, 0.020)
        + 0.35 * ratio_to_100(run_g_ratio, 0.015, 0.320)
        + 0.20 * ratio_to_100(max(lat_peak_ratio, z_peak_ratio), 0.10, 0.50)
    )

    baseline_stats = _baseline_stats(features)
    baseline_count = len([key for key in ROPE_BASELINE_KEYS if key in baseline_stats])
    baseline_weight = _normalize_weight(baseline_count, len(ROPE_BASELINE_KEYS))
    fallback = _branch_fallback_components(features)
    robust = _branch_robust_components(features, baseline_stats) if baseline_stats else {key: 0.0 for key in fallback.keys()}
    mixed = _mix_components(baseline_weight, robust, fallback)

    loose_score = (
        0.30 * mixed["loose_lateral"]
        + 0.24 * mixed["loose_lowfreq"]
        + 0.24 * mixed["loose_coupling"]
        + 0.22 * mixed["loose_energy"]
    )
    tight_score = (
        0.36 * mixed["tight_vertical"]
        + 0.34 * mixed["tight_dynamic"]
        + 0.30 * mixed["tight_spectral"]
    )
    dominant_branch = "loose_like" if loose_score >= tight_score else "tight_like"
    branch_score = max(loose_score, tight_score)
    rule_score = clamp(branch_score - 0.30 * mixed["spiky_penalty"], 0.0, 100.0)

    gate_rescue_score = branch_score - 0.25 * mixed["spiky_penalty"]
    effective_run_score = max(run_state_score, gate_rescue_score)
    gate_mode = "running"
    if effective_run_score < 30.0:
        rule_score *= 0.40
        gate_mode = "non_running_suppressed"
    elif effective_run_score < 45.0:
        rule_score *= 0.72
        gate_mode = "weak_running_suppressed"
    rule_score = clamp(rule_score, 0.0, 100.0)

    if dominant_branch == "loose_like":
        branch_pass = (
            rule_score >= 62.0
            and mixed["loose_lateral"] >= 32.0
            and mixed["loose_coupling"] >= 28.0
            and mixed["loose_lowfreq"] >= 22.0
            and mixed["spiky_penalty"] <= 58.0
            and effective_run_score >= 35.0
        )
    else:
        branch_pass = (
            rule_score >= 60.0
            and mixed["tight_vertical"] >= 34.0
            and mixed["tight_dynamic"] >= 32.0
            and mixed["tight_spectral"] >= 28.0
            and mixed["spiky_penalty"] <= 58.0
            and effective_run_score >= 35.0
        )
    confirm_mode = f"{dominant_branch}_pass" if branch_pass else "suppressed_single_window"
    if not branch_pass:
        rule_score = min(rule_score, 59.0)

    return {
        "baseline_count": baseline_count,
        "baseline_weight": baseline_weight,
        "baseline_mode": "robust_baseline" if baseline_weight > 0.0 else "self_normalized_fallback",
        "run_state_score": float(run_state_score),
        "effective_run_score": float(effective_run_score),
        "gate_rescue_score": float(gate_rescue_score),
        "gate_mode": gate_mode,
        "confirm_mode": confirm_mode,
        "fallback": fallback,
        "robust": robust,
        "mixed": mixed,
        "loose_score": float(loose_score),
        "tight_score": float(tight_score),
        "dominant_branch": dominant_branch,
        "rule_score": float(rule_score),
        "model_row": _build_model_row(features, mixed, baseline_stats),
        "spectral_snapshot": {
            "lat_dom_freq_hz": round(mixed["lat_dom_freq_hz"], 4),
            "lat_peak_ratio": round(mixed["lat_peak_ratio"], 4),
            "lat_low_band_ratio": round(mixed["lat_low_band_ratio"], 4),
            "z_dom_freq_hz": round(mixed["z_dom_freq_hz"], 4),
            "z_peak_ratio": round(mixed["z_peak_ratio"], 4),
            "z_low_band_ratio": round(mixed["z_low_band_ratio"], 4),
        },
    }


def detect(features: dict[str, Any]) -> dict[str, Any]:
    analysis = _analyze_rope_signature(features)
    model_probability = 0.0
    model = _load_model(features)
    if model is not None:
        proba = model.predict_proba_vec([analysis["model_row"][name] for name in model.feature_names])
        model_probability = float(proba.get(FAULT_TYPE, 0.0))

    if model is not None:
        score = 0.65 * (model_probability * 100.0) + 0.35 * float(analysis["rule_score"])
    else:
        score = float(analysis["rule_score"])
    if str(analysis["confirm_mode"]) == "suppressed_single_window":
        score = min(score, 59.0)
    score = clamp(score, 0.0, 100.0)

    reasons = [
        "mode=rope_tension_abnormal_v2",
        f"baseline_mode={analysis['baseline_mode']}",
        f"baseline_features={analysis['baseline_count']}",
        f"baseline_weight={analysis['baseline_weight']:.3f}",
        f"gate={analysis['gate_mode']}",
        f"confirm={analysis['confirm_mode']}",
        f"rope_branch={analysis['dominant_branch']}",
        f"rope_rule_score={analysis['rule_score']:.2f}",
        f"rope_model_probability={model_probability:.4f}",
        f"score_loose_like={analysis['loose_score']:.2f}",
        f"score_tight_like={analysis['tight_score']:.2f}",
        f"run_state_score={analysis['run_state_score']:.2f}",
        f"effective_run_score={analysis['effective_run_score']:.2f}",
        f"gate_rescue_score={analysis['gate_rescue_score']:.2f}",
        f"component_loose_lateral={analysis['mixed']['loose_lateral']:.2f}",
        f"component_loose_lowfreq={analysis['mixed']['loose_lowfreq']:.2f}",
        f"component_loose_coupling={analysis['mixed']['loose_coupling']:.2f}",
        f"component_loose_energy={analysis['mixed']['loose_energy']:.2f}",
        f"component_tight_vertical={analysis['mixed']['tight_vertical']:.2f}",
        f"component_tight_dynamic={analysis['mixed']['tight_dynamic']:.2f}",
        f"component_tight_spectral={analysis['mixed']['tight_spectral']:.2f}",
        f"spiky_penalty={analysis['mixed']['spiky_penalty']:.2f}",
        f"lat_dom_freq_hz={analysis['mixed']['lat_dom_freq_hz']:.4f}",
        f"lat_peak_ratio={analysis['mixed']['lat_peak_ratio']:.4f}",
        f"lat_low_band_ratio={analysis['mixed']['lat_low_band_ratio']:.4f}",
        f"z_dom_freq_hz={analysis['mixed']['z_dom_freq_hz']:.4f}",
        f"z_peak_ratio={analysis['mixed']['z_peak_ratio']:.4f}",
        f"z_low_band_ratio={analysis['mixed']['z_low_band_ratio']:.4f}",
    ]

    result = build_result(
        fault_type=FAULT_TYPE,
        score=score,
        reasons=reasons,
        features=features,
        min_samples=8,
        penalize_low_fs=False,
    )
    result["rope_rule_score"] = round(float(analysis["rule_score"]), 2)
    result["rope_model_probability"] = round(float(model_probability), 4)
    result["rope_branch"] = str(analysis["dominant_branch"])
    result["rope_spectral_snapshot"] = dict(analysis["spectral_snapshot"])
    return result


def _select_csv_files(root: Path) -> list[Path]:
    if not root.exists():
        raise FileNotFoundError(f"dataset dir not found: {root}")
    preferred = sorted(root.rglob("vibration_30s_*.csv"))
    if preferred:
        return preferred
    return sorted(root.rglob("*.csv"))


def _build_baseline_from_dir(root: Path) -> dict[str, Any]:
    feature_rows = [build_feature_pack(load_rows(path)) for path in _select_csv_files(root)]
    baseline = build_clean_feature_baseline(feature_rows, ROPE_BASELINE_KEYS, min_samples=8)
    baseline["source"] = str(root)
    return baseline


def _dataset_samples(dir_path: Path, *, label: str, baseline: dict[str, Any]) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for path in _select_csv_files(dir_path):
        features = build_feature_pack(load_rows(path))
        features["baseline"] = baseline
        analysis = _analyze_rope_signature(features)
        samples.append(
            {
                "path": str(path),
                "name": path.name,
                "label": label,
                "features": dict(analysis["model_row"]),
                "result": detect(features),
            }
        )
    return samples


def _fit_model_from_samples(samples: list[dict[str, Any]]) -> "CentroidModel":
    if CentroidModel is None or fit_centroid_classifier is None:
        raise RuntimeError("centroid model support is unavailable")
    feature_rows = [sample["features"] for sample in samples]
    labels = [str(sample["label"]) for sample in samples]
    matrix = [[_safe_float(row.get(name), 0.0) for name in ROPE_MODEL_FIELDS] for row in feature_rows]
    return fit_centroid_classifier(
        features=matrix,
        labels=labels,
        feature_names=list(ROPE_MODEL_FIELDS),
        task="rope_tension_abnormal",
    )


def _evaluate_group(dir_path: Path, *, baseline: dict[str, Any], model: "CentroidModel", expected_positive: bool) -> dict[str, Any]:
    paths = _select_csv_files(dir_path)
    windows: list[dict[str, Any]] = []
    candidate_count = 0
    watch_count = 0
    branch_counts = {"loose_like": 0, "tight_like": 0}

    for path in paths:
        features = build_feature_pack(load_rows(path))
        features["baseline"] = baseline
        features["rope_model"] = model
        result = detect(features)
        score = _safe_float(result.get("score"), 0.0)
        branch = str(result.get("rope_branch", ""))
        if branch in branch_counts:
            branch_counts[branch] += 1
        if score >= MIN_SCORE_TRIGGER and bool(result.get("triggered", False)):
            candidate_count += 1
        elif score >= MIN_SCORE_WATCH:
            watch_count += 1
        windows.append({"file": path.name, "score": round(score, 2), "branch": branch, "triggered": bool(result.get("triggered", False))})

    count = len(windows)
    candidate_ratio = candidate_count / max(1, count)
    watch_ratio = watch_count / max(1, count)
    if candidate_ratio >= 0.30 or candidate_count >= 2:
        group_status = "candidate_faults"
    elif watch_ratio >= 0.30 or watch_count >= 2:
        group_status = "watch_only"
    else:
        group_status = "normal"

    pass_condition = group_status != "normal" if expected_positive else candidate_count == 0
    return {
        "group": dir_path.name,
        "count": count,
        "candidate_count": candidate_count,
        "watch_count": watch_count,
        "candidate_ratio": round(candidate_ratio, 4),
        "watch_ratio": round(watch_ratio, 4),
        "group_status": group_status,
        "expected_positive": expected_positive,
        "pass": pass_condition,
        "branch_distribution": branch_counts,
        "windows": windows,
    }


def _cross_validate(data_root: Path, output_model: Path) -> dict[str, Any]:
    required_dirs = {
        "01_normal": data_root / "01_normal",
        "02_normal": data_root / "02_normal",
        "01_gangsisheng": data_root / "01_gangsisheng",
        "02_gangsisheng": data_root / "02_gangsisheng",
    }
    optional_dirs = {
        "01_xiangjiaoquan": data_root / "01_xiangjiaoquan",
        "02_xiangjiaoquan": data_root / "02_xiangjiaoquan",
    }
    for name, path in required_dirs.items():
        if not path.exists():
            raise FileNotFoundError(f"missing validation dir: {name} -> {path}")

    fold_specs = [
        {
            "name": "fold_01_to_02",
            "train_normal": required_dirs["01_normal"],
            "train_rope": required_dirs["01_gangsisheng"],
            "eval_normal": required_dirs["02_normal"],
            "eval_rope": required_dirs["02_gangsisheng"],
            "eval_rubber": optional_dirs["02_xiangjiaoquan"],
        },
        {
            "name": "fold_02_to_01",
            "train_normal": required_dirs["02_normal"],
            "train_rope": required_dirs["02_gangsisheng"],
            "eval_normal": required_dirs["01_normal"],
            "eval_rope": required_dirs["01_gangsisheng"],
            "eval_rubber": optional_dirs["01_xiangjiaoquan"],
        },
    ]

    folds: list[dict[str, Any]] = []
    for spec in fold_specs:
        train_baseline = _build_baseline_from_dir(spec["train_normal"])
        train_samples = _dataset_samples(spec["train_normal"], label="normal", baseline=train_baseline)
        train_samples.extend(_dataset_samples(spec["train_rope"], label=FAULT_TYPE, baseline=train_baseline))
        model = _fit_model_from_samples(train_samples)

        eval_baseline = _build_baseline_from_dir(spec["eval_normal"])
        normal_metrics = _evaluate_group(spec["eval_normal"], baseline=eval_baseline, model=model, expected_positive=False)
        rope_metrics = _evaluate_group(spec["eval_rope"], baseline=eval_baseline, model=model, expected_positive=True)
        rubber_metrics = None
        if spec["eval_rubber"].exists():
            rubber_metrics = _evaluate_group(spec["eval_rubber"], baseline=eval_baseline, model=model, expected_positive=False)

        folds.append(
            {
                "name": spec["name"],
                "train_baseline_cleaning": dict(train_baseline.get("cleaning", {})),
                "eval_baseline_cleaning": dict(eval_baseline.get("cleaning", {})),
                "train_samples": len(train_samples),
                "normal": normal_metrics,
                "rope": rope_metrics,
                "rubber": rubber_metrics,
                "pass": bool(normal_metrics["pass"] and rope_metrics["pass"] and (rubber_metrics is None or rubber_metrics["pass"])),
            }
        )

    baseline_01 = _build_baseline_from_dir(required_dirs["01_normal"])
    baseline_02 = _build_baseline_from_dir(required_dirs["02_normal"])
    final_samples = _dataset_samples(required_dirs["01_normal"], label="normal", baseline=baseline_01)
    final_samples.extend(_dataset_samples(required_dirs["01_gangsisheng"], label=FAULT_TYPE, baseline=baseline_01))
    final_samples.extend(_dataset_samples(required_dirs["02_normal"], label="normal", baseline=baseline_02))
    final_samples.extend(_dataset_samples(required_dirs["02_gangsisheng"], label=FAULT_TYPE, baseline=baseline_02))
    final_model = _fit_model_from_samples(final_samples)
    final_model.metrics["cross_validation"] = folds
    final_model.metrics["positive_label"] = FAULT_TYPE
    final_model.metrics["negative_label"] = "normal"
    final_model.metrics["sample_count"] = len(final_samples)
    final_model.save(str(output_model))

    return {
        "data_root": str(data_root),
        "output_model": str(output_model),
        "folds": folds,
        "final_model_metrics": dict(final_model.metrics),
        "pass": all(bool(fold["pass"]) for fold in folds),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="钢丝绳状态异常识别与跨梯验证")
    parser.add_argument("--input", default="", help="输入 CSV，用于单文件诊断")
    parser.add_argument("--pretty", action="store_true", help="格式化输出 JSON")
    parser.add_argument("--cross-validate", action="store_true", help="运行 1号梯<->2号梯 跨梯验证并训练最终模型")
    parser.add_argument("--data-root", default="", help="跨梯验证数据根目录，需包含 01_normal/02_normal/01_gangsisheng/02_gangsisheng")
    parser.add_argument("--output-model", default=str(DEFAULT_MODEL_PATH), help="跨梯验证完成后输出模型 JSON")
    args = parser.parse_args()

    if args.cross_validate:
        if not str(args.data_root).strip():
            raise SystemExit("--data-root is required with --cross-validate")
        payload = _cross_validate(Path(args.data_root).expanduser().resolve(), Path(args.output_model).expanduser().resolve())
        if args.pretty:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(payload, ensure_ascii=False))
        return 0

    if not str(args.input).strip():
        raise SystemExit("--input is required unless --cross-validate is used")
    rows = load_rows(Path(args.input))
    features = build_feature_pack(rows)
    result = detect(features)
    if args.pretty:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
