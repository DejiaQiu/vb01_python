"""钢丝绳状态异常单窗诊断。

当前版本收敛成“少量核心特征 + 少量通用异常特征”的规则链：

1. 核心特征只保留 4 类：
   - 横向比例 `lateral_ratio`
   - 横向主频偏移 `lat_dom_freq_hz`
   - 横向低频占比 `lat_low_band_ratio`
   - 加速度/角速度耦合聚合量 `corr_major`
2. 通用异常只保留 3 类：
   - `a_rms_ac`
   - `a_p2p`
   - `g_std`
3. Z 向和竖向谱特征只用于 watch 级别提示和解释，不允许单独把样本推成高置信 rope 候选。

当前只保留规则链路，不再在钢丝绳专项里保留 centroid 训练、加载或融合逻辑。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

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

ROPE_BASELINE_KEYS = (
    "a_rms_ac",
    "a_p2p",
    "g_std",
    "lateral_ratio",
    "ag_corr",
    "gx_ax_corr",
    "gy_ay_corr",
    "peak_rate_hz",
    "a_crest",
    "a_kurt",
    "energy_z_over_xy",
    "az_p2p",
    "az_jerk_rms",
    "lat_dom_freq_hz",
    "lat_low_band_ratio",
    "z_dom_freq_hz",
    "z_peak_ratio",
)

ROPE_RULE_CONFIG = {
    "watch_score": 52.0,
    "candidate_score": 72.0,
    "feature_hit_min": 45.0,
    "feature_strong_min": 60.0,
    "watch_hit_min": 2,
    "candidate_hit_min": 3,
    "candidate_strong_min": 2,
    "watch_run_min": 30.0,
    "candidate_run_min": 35.0,
    "spiky_penalty_max": 60.0,
    "watch_score_cap": 59.0,
}

MIN_SCORE_WATCH = ROPE_RULE_CONFIG["watch_score"]
MIN_SCORE_TRIGGER = ROPE_RULE_CONFIG["candidate_score"]

EPS = 1e-9


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
    a_rms_ac = _to_float(features.get("a_rms_ac"))
    az_rms_ac = max(abs(_to_float(features.get("az_rms_ac"), 0.01)), 1e-4)
    lateral_ratio = _to_float(features.get("lateral_ratio"), 1.0)
    a_p2p = _to_float(features.get("a_p2p"))
    g_std = _to_float(features.get("g_std"))
    peak_rate_hz = _to_float(features.get("peak_rate_hz"))
    a_crest = _to_float(features.get("a_crest"))
    a_kurt = _to_float(features.get("a_kurt"))
    energy_z_over_xy = _to_float(features.get("energy_z_over_xy"), 1.0)
    az_p2p = _to_float(features.get("az_p2p"))
    az_jerk_rms = _to_float(features.get("az_jerk_rms"))
    ag_corr = _to_float(features.get("ag_corr"))
    gx_ax_corr = _to_float(features.get("gx_ax_corr"))
    gy_ay_corr = _to_float(features.get("gy_ay_corr"))
    lat_dom_freq_hz = _to_float(features.get("lat_dom_freq_hz"))
    lat_peak_ratio = _to_float(features.get("lat_peak_ratio"))
    lat_low_band_ratio = _to_float(features.get("lat_low_band_ratio"))
    z_dom_freq_hz = _to_float(features.get("z_dom_freq_hz"))
    z_peak_ratio = _to_float(features.get("z_peak_ratio"))
    corr_major = max(ag_corr, gx_ax_corr, gy_ay_corr)

    rope_lateral = ratio_to_100(lateral_ratio, 1.05, 1.70)
    rope_domfreq = 100.0 - ratio_to_100(lat_dom_freq_hz, 1.40, 4.60)
    rope_lowband = ratio_to_100(lat_low_band_ratio, 0.22, 0.70)
    rope_coupling = ratio_to_100(corr_major, 0.10, 0.60)
    shared_abnormal = (
        ratio_to_100(a_rms_ac / max(a_mean, EPS), 0.0015, 0.020)
        + ratio_to_100(a_p2p / max(a_mean, EPS), 0.020, 0.180)
        + ratio_to_100(g_std / max(g_mean, EPS), 0.020, 0.260)
    ) / 3.0
    confounding_score = (
        ratio_to_100(energy_z_over_xy, 0.95, 1.85)
        + ratio_to_100(az_p2p, 0.10, 0.28)
        + ratio_to_100(az_jerk_rms / max(az_rms_ac, EPS), 0.85, 2.50)
        + ratio_to_100(z_peak_ratio, 0.16, 0.58)
        + ratio_to_100(abs(z_dom_freq_hz - 2.5), 0.10, 3.00)
    ) / 5.0

    spiky_penalty = (
        0.40 * ratio_to_100(a_crest, 1.8, 4.2)
        + 0.35 * ratio_to_100(a_kurt, 1.0, 6.0)
        + 0.25 * ratio_to_100(peak_rate_hz, 0.40, 4.00)
    )

    return {
        "rope_lateral": float(rope_lateral),
        "rope_domfreq": float(rope_domfreq),
        "rope_lowband": float(rope_lowband),
        "rope_coupling": float(rope_coupling),
        "shared_abnormal": float(shared_abnormal),
        "confounding_score": float(confounding_score),
        "spiky_penalty": float(spiky_penalty),
        "corr_major": float(corr_major),
        "lat_dom_freq_hz": float(lat_dom_freq_hz),
        "lat_peak_ratio": float(lat_peak_ratio),
        "lat_low_band_ratio": float(lat_low_band_ratio),
        "z_dom_freq_hz": float(z_dom_freq_hz),
        "z_peak_ratio": float(z_peak_ratio),
        "z_low_band_ratio": 0.0,
    }


def _branch_robust_components(features: dict[str, Any], baseline_stats: dict[str, tuple[float, float]]) -> dict[str, float]:
    lateral_ratio = _to_float(features.get("lateral_ratio"), 1.0)
    a_rms_ac = _to_float(features.get("a_rms_ac"))
    a_p2p = _to_float(features.get("a_p2p"))
    g_std = _to_float(features.get("g_std"))
    peak_rate_hz = _to_float(features.get("peak_rate_hz"))
    a_crest = _to_float(features.get("a_crest"))
    a_kurt = _to_float(features.get("a_kurt"))
    energy_z_over_xy = _to_float(features.get("energy_z_over_xy"), 1.0)
    az_p2p = _to_float(features.get("az_p2p"))
    az_jerk_rms = _to_float(features.get("az_jerk_rms"))
    ag_corr = _to_float(features.get("ag_corr"))
    gx_ax_corr = _to_float(features.get("gx_ax_corr"))
    gy_ay_corr = _to_float(features.get("gy_ay_corr"))
    lat_dom_freq_hz = _to_float(features.get("lat_dom_freq_hz"))
    lat_peak_ratio = _to_float(features.get("lat_peak_ratio"))
    lat_low_band_ratio = _to_float(features.get("lat_low_band_ratio"))
    z_dom_freq_hz = _to_float(features.get("z_dom_freq_hz"))
    z_peak_ratio = _to_float(features.get("z_peak_ratio"))
    corr_major = max(ag_corr, gx_ax_corr, gy_ay_corr)

    rope_lateral = _z_to_100(_positive_z(lateral_ratio, baseline_stats.get("lateral_ratio")))
    rope_domfreq = _z_to_100(_negative_z(lat_dom_freq_hz, baseline_stats.get("lat_dom_freq_hz")))
    rope_lowband = _z_to_100(_positive_z(lat_low_band_ratio, baseline_stats.get("lat_low_band_ratio")))
    rope_coupling = _z_to_100(max(
        _positive_z(ag_corr, baseline_stats.get("ag_corr")),
        _positive_z(gx_ax_corr, baseline_stats.get("gx_ax_corr")),
        _positive_z(gy_ay_corr, baseline_stats.get("gy_ay_corr")),
    ))
    shared_abnormal = _z_to_100((
        _positive_z(a_rms_ac, baseline_stats.get("a_rms_ac"))
        + _positive_z(a_p2p, baseline_stats.get("a_p2p"))
        + _positive_z(g_std, baseline_stats.get("g_std"))
    ) / 3.0)
    confounding_score = _z_to_100((
        _positive_z(energy_z_over_xy, baseline_stats.get("energy_z_over_xy"))
        + _positive_z(az_p2p, baseline_stats.get("az_p2p"))
        + _positive_z(az_jerk_rms, baseline_stats.get("az_jerk_rms"))
        + _positive_z(z_peak_ratio, baseline_stats.get("z_peak_ratio"))
        + _abs_shift_z(z_dom_freq_hz, baseline_stats.get("z_dom_freq_hz"))
    ) / 5.0)
    spiky_penalty = _z_to_100(
        0.40 * _positive_z(a_crest, baseline_stats.get("a_crest"))
        + 0.35 * _positive_z(a_kurt, baseline_stats.get("a_kurt"))
        + 0.25 * _positive_z(peak_rate_hz, baseline_stats.get("peak_rate_hz"))
    )

    return {
        "rope_lateral": float(rope_lateral),
        "rope_domfreq": float(rope_domfreq),
        "rope_lowband": float(rope_lowband),
        "rope_coupling": float(rope_coupling),
        "shared_abnormal": float(shared_abnormal),
        "confounding_score": float(confounding_score),
        "spiky_penalty": float(spiky_penalty),
        "corr_major": float(corr_major),
        "lat_dom_freq_hz": float(lat_dom_freq_hz),
        "lat_peak_ratio": float(lat_peak_ratio),
        "lat_low_band_ratio": float(lat_low_band_ratio),
        "z_dom_freq_hz": float(z_dom_freq_hz),
        "z_peak_ratio": float(z_peak_ratio),
        "z_low_band_ratio": 0.0,
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




def _count_hits(values: list[float], min_score: float) -> int:
    return sum(1 for value in values if float(value) >= float(min_score))


def _analyze_rope_signature(features: dict[str, Any]) -> dict[str, Any]:
    rule_cfg = ROPE_RULE_CONFIG
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

    loose_score = (mixed["rope_lateral"] + mixed["rope_domfreq"] + mixed["rope_lowband"]) / 3.0
    tight_score = (mixed["rope_coupling"] + mixed["shared_abnormal"]) / 2.0
    dominant_branch = "loose_like" if loose_score >= tight_score else "tight_like"
    rope_specific_score = (
        mixed["rope_lateral"] + mixed["rope_domfreq"] + mixed["rope_lowband"] + mixed["rope_coupling"]
    ) / 4.0
    baseline_deviation_score = float(mixed["shared_abnormal"])
    core_values = [
        mixed["rope_lateral"],
        mixed["rope_domfreq"],
        mixed["rope_lowband"],
        mixed["rope_coupling"],
    ]
    core_hits = _count_hits(core_values, rule_cfg["feature_hit_min"])
    core_strong_hits = _count_hits(core_values, rule_cfg["feature_strong_min"])
    candidate_signal = 0.0
    watch_signal = 0.0

    gate_rescue_score = rope_specific_score
    effective_run_score = max(run_state_score, gate_rescue_score)
    gate_mode = "running"
    if effective_run_score < rule_cfg["watch_run_min"]:
        gate_mode = "non_running_suppressed"
    elif effective_run_score < 45.0:
        gate_mode = "weak_running_suppressed"

    candidate_ready = (
        core_hits >= rule_cfg["candidate_hit_min"]
        and core_strong_hits >= rule_cfg["candidate_strong_min"]
        and effective_run_score >= rule_cfg["candidate_run_min"]
        and mixed["spiky_penalty"] <= rule_cfg["spiky_penalty_max"]
    )
    watch_ready = (
        core_hits >= rule_cfg["watch_hit_min"]
        and effective_run_score >= rule_cfg["watch_run_min"]
    )

    if candidate_ready:
        confirm_mode = "candidate_hits_pass"
        candidate_signal = clamp(
            rule_cfg["candidate_score"] + 4.0 * max(0, core_strong_hits - rule_cfg["candidate_strong_min"]) + 2.0 * max(0, core_hits - rule_cfg["candidate_hit_min"]),
            0.0,
            100.0,
        )
        watch_signal = candidate_signal
        rule_score = candidate_signal
    elif watch_ready:
        confirm_mode = "watch_hits_pass"
        watch_signal = clamp(
            rule_cfg["watch_score"] + 3.0 * max(0, core_hits - rule_cfg["watch_hit_min"]) + 2.0 * max(0, core_strong_hits - 1),
            rule_cfg["watch_score"],
            rule_cfg["watch_score_cap"],
        )
        rule_score = watch_signal
    else:
        confirm_mode = "suppressed_single_window"
        watch_signal = 20.0 + 6.0 * core_hits + 3.0 * core_strong_hits
        rule_score = min(watch_signal, 44.0)

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
        "baseline_deviation_score": float(baseline_deviation_score),
        "rope_specific_score": float(rope_specific_score),
        "candidate_signal": float(candidate_signal),
        "watch_signal": float(watch_signal),
        "loose_score": float(loose_score),
        "tight_score": float(tight_score),
        "core_hits": int(core_hits),
        "core_strong_hits": int(core_strong_hits),
        "dominant_branch": dominant_branch,
        "rule_score": float(rule_score),
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
    score = float(analysis["rule_score"])
    if str(analysis["confirm_mode"]) not in {"candidate_hits_pass", "watch_hits_pass"}:
        score = min(score, 59.0)
    score = clamp(score, 0.0, 100.0)

    reasons = [
        "mode=rope_tension_abnormal_v3_hits",
        f"baseline_mode={analysis['baseline_mode']}",
        f"baseline_features={analysis['baseline_count']}",
        f"baseline_weight={analysis['baseline_weight']:.3f}",
        f"gate={analysis['gate_mode']}",
        f"confirm={analysis['confirm_mode']}",
        f"rope_branch={analysis['dominant_branch']}",
        f"core_hits={analysis['core_hits']}",
        f"core_strong_hits={analysis['core_strong_hits']}",
        f"rope_rule_score={analysis['rule_score']:.2f}",
        f"baseline_deviation_score={analysis['baseline_deviation_score']:.2f}",
        f"rope_specific_score={analysis['rope_specific_score']:.2f}",
        f"candidate_signal={analysis['candidate_signal']:.2f}",
        f"watch_signal={analysis['watch_signal']:.2f}",
        f"score_loose_like={analysis['loose_score']:.2f}",
        f"score_tight_like={analysis['tight_score']:.2f}",
        f"run_state_score={analysis['run_state_score']:.2f}",
        f"effective_run_score={analysis['effective_run_score']:.2f}",
        f"gate_rescue_score={analysis['gate_rescue_score']:.2f}",
        f"component_rope_lateral={analysis['mixed']['rope_lateral']:.2f}",
        f"component_rope_domfreq={analysis['mixed']['rope_domfreq']:.2f}",
        f"component_rope_lowband={analysis['mixed']['rope_lowband']:.2f}",
        f"component_rope_coupling={analysis['mixed']['rope_coupling']:.2f}",
        f"component_shared_abnormal={analysis['mixed']['shared_abnormal']:.2f}",
        f"component_confounding={analysis['mixed']['confounding_score']:.2f}",
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
    result["rope_branch"] = str(analysis["dominant_branch"])
    result["rope_spectral_snapshot"] = dict(analysis["spectral_snapshot"])
    result["detector_family"] = "rope"
    result["baseline_mode"] = str(analysis["baseline_mode"])
    result["baseline_deviation_score"] = round(float(analysis["baseline_deviation_score"]), 2)
    result["rope_specific_score"] = round(float(analysis["rope_specific_score"]), 2)
    result["shared_abnormal_score"] = round(float(analysis["mixed"]["shared_abnormal"]), 2)
    result["confounding_score"] = round(float(analysis["mixed"]["confounding_score"]), 2)
    result["spiky_penalty_score"] = round(float(analysis["mixed"]["spiky_penalty"]), 2)
    result["run_state_score"] = round(float(analysis["run_state_score"]), 2)
    result["effective_run_score"] = round(float(analysis["effective_run_score"]), 2)
    result["core_hits"] = int(analysis["core_hits"])
    result["core_strong_hits"] = int(analysis["core_strong_hits"])
    result["specialized_ready"] = str(analysis["confirm_mode"]) == "candidate_hits_pass"
    result["type_watch_ready"] = str(analysis["confirm_mode"]) in {"candidate_hits_pass", "watch_hits_pass"}
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

def _evaluate_group(dir_path: Path, *, baseline: dict[str, Any], expected_positive: bool) -> dict[str, Any]:
    paths = _select_csv_files(dir_path)
    windows: list[dict[str, Any]] = []
    candidate_count = 0
    watch_count = 0
    branch_counts = {"loose_like": 0, "tight_like": 0}

    for path in paths:
        features = build_feature_pack(load_rows(path))
        features["baseline"] = baseline
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


def _cross_validate(data_root: Path) -> dict[str, Any]:
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
        eval_baseline = _build_baseline_from_dir(spec["eval_normal"])
        normal_metrics = _evaluate_group(spec["eval_normal"], baseline=eval_baseline, expected_positive=False)
        rope_metrics = _evaluate_group(spec["eval_rope"], baseline=eval_baseline, expected_positive=True)
        rubber_metrics = None
        if spec["eval_rubber"].exists():
            rubber_metrics = _evaluate_group(spec["eval_rubber"], baseline=eval_baseline, expected_positive=False)

        folds.append(
            {
                "name": spec["name"],
                "train_baseline_cleaning": dict(train_baseline.get("cleaning", {})),
                "eval_baseline_cleaning": dict(eval_baseline.get("cleaning", {})),
                "normal": normal_metrics,
                "rope": rope_metrics,
                "rubber": rubber_metrics,
                "pass": bool(normal_metrics["pass"] and rope_metrics["pass"] and (rubber_metrics is None or rubber_metrics["pass"])),
            }
        )

    return {
        "data_root": str(data_root),
        "mode": "rules_only",
        "folds": folds,
        "pass": all(bool(fold["pass"]) for fold in folds),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="钢丝绳状态异常识别与跨梯规则验证")
    parser.add_argument("--input", default="", help="输入 CSV，用于单文件诊断")
    parser.add_argument("--pretty", action="store_true", help="格式化输出 JSON")
    parser.add_argument("--cross-validate", action="store_true", help="运行 1号梯<->2号梯 跨梯规则验证")
    parser.add_argument("--data-root", default="", help="跨梯验证数据根目录，需包含 01_normal/02_normal/01_gangsisheng/02_gangsisheng")
    args = parser.parse_args()

    if args.cross_validate:
        if not str(args.data_root).strip():
            raise SystemExit("--data-root is required with --cross-validate")
        payload = _cross_validate(Path(args.data_root).expanduser().resolve())
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
