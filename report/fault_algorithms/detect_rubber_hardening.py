"""橡胶圈硬化单窗诊断。

当前版本收敛成少量核心特征：

1. 竖向传递增强：`energy_z_over_xy`、`az_p2p`
2. 阻尼退化代理量：`az_cv`、`az_jerk_rms`
3. 轴间耦合变化：`corr_xy / corr_xz / corr_yz`
4. 通用异常强度：`a_rms_ac`、`a_p2p`、`g_std`
5. 冲击抑制：`a_crest`、`a_kurt`、`peak_rate_hz`

目标是用更少、更稳定的规则，把 rubber 约束在“竖向 + 阻尼 + 耦合”画像里。
"""

from __future__ import annotations

import math

try:
    from ._base import build_result, clamp, parse_float, ratio_to_100, run_detector_cli
except ImportError:  # pragma: no cover
    from _base import build_result, clamp, parse_float, ratio_to_100, run_detector_cli


FAULT_TYPE = "rubber_hardening"

RUBBER_BASELINE_KEYS = (
    "a_rms_ac",
    "a_p2p",
    "g_std",
    "energy_z_over_xy",
    "az_p2p",
    "az_cv",
    "az_jerk_rms",
    "az_std",
    "corr_xy",
    "corr_xz",
    "corr_yz",
    "a_crest",
    "a_kurt",
    "peak_rate_hz",
)

RUBBER_RULE_CONFIG = {
    "watch_score": 52.0,
    "candidate_score": 72.0,
    "feature_hit_min": 45.0,
    "feature_strong_min": 60.0,
    "watch_hit_min": 2,
    "candidate_hit_min": 3,
    "candidate_strong_min": 2,
    "watch_run_min": 30.0,
    "candidate_run_min": 35.0,
    "watch_score_cap": 59.0,
    "spiky_penalty_max": 60.0,
}

EPS = 1e-9


def _to_float(value: object, default: float = 0.0) -> float:
    parsed = parse_float(value)
    return float(parsed if parsed is not None else default)


def _baseline_stats(features: dict) -> dict[str, tuple[float, float]]:
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


def _count_hits(values: list[float], min_score: float) -> int:
    return sum(1 for value in values if float(value) >= float(min_score))


def detect(features: dict) -> dict:
    rule_cfg = RUBBER_RULE_CONFIG
    a_mean = max(abs(_to_float(features.get("a_mean"), 1.0)), 1e-3)
    g_mean = max(abs(_to_float(features.get("g_mean"), 0.3)), 0.05)
    a_rms_ac = _to_float(features.get("a_rms_ac"))
    a_p2p = _to_float(features.get("a_p2p"))
    a_crest = _to_float(features.get("a_crest"))
    a_kurt = _to_float(features.get("a_kurt"))
    g_std = _to_float(features.get("g_std"))
    peak_rate_hz = _to_float(features.get("peak_rate_hz"))

    az_p2p = _to_float(features.get("az_p2p"))
    az_std = _to_float(features.get("az_std"))
    az_cv = _to_float(features.get("az_cv"))
    az_jerk_rms = _to_float(features.get("az_jerk_rms"))
    corr_xy = _to_float(features.get("corr_xy"))
    corr_xz = _to_float(features.get("corr_xz"))
    corr_yz = _to_float(features.get("corr_yz"))
    energy_z_over_xy = _to_float(features.get("energy_z_over_xy"), 1.0)
    corr_major = max(abs(corr_xy), abs(corr_xz), abs(corr_yz))

    run_a_ratio = a_rms_ac / max(a_mean, EPS)
    run_g_ratio = g_std / max(g_mean, EPS)
    run_state_score = (
        0.55 * ratio_to_100(run_a_ratio, 0.0010, 0.014)
        + 0.45 * ratio_to_100(run_g_ratio, 0.010, 0.220)
    )

    fallback_vertical = (
        ratio_to_100(energy_z_over_xy, 0.85, 1.85)
        + ratio_to_100(max(az_p2p, az_std), 0.038, 0.062)
    ) / 2.0
    fallback_directional = fallback_vertical
    fallback_coupling = ratio_to_100(corr_major, 0.20, 0.90)
    fallback_damping = (
        ratio_to_100(az_cv, 0.80, 1.60)
        + ratio_to_100(az_jerk_rms, 0.014, 0.032)
    ) / 2.0
    fallback_spiky = (
        0.40 * ratio_to_100(a_crest, 1.8, 4.2)
        + 0.35 * ratio_to_100(a_kurt, 1.0, 6.0)
        + 0.25 * ratio_to_100(peak_rate_hz, 0.40, 3.50)
    )
    fallback_shared_abnormal = (
        ratio_to_100(a_rms_ac / max(a_mean, EPS), 0.0010, 0.012)
        + ratio_to_100(a_p2p / max(a_mean, EPS), 0.020, 0.180)
        + ratio_to_100(g_std / max(g_mean, EPS), 0.010, 0.220)
    ) / 3.0

    baseline_stats = _baseline_stats(features)
    baseline_count = len([key for key in RUBBER_BASELINE_KEYS if key in baseline_stats])
    baseline_weight = _normalize_weight(baseline_count, len(RUBBER_BASELINE_KEYS))
    baseline_mode = "robust_baseline" if baseline_weight > 0.0 else "self_normalized_fallback"

    robust_vertical = _z_to_100((
        _positive_z(energy_z_over_xy, baseline_stats.get("energy_z_over_xy"))
        + max(
            _positive_z(az_p2p, baseline_stats.get("az_p2p")),
            _positive_z(az_std, baseline_stats.get("az_std")),
        )
    ) / 2.0)
    robust_directional = robust_vertical
    robust_coupling = _z_to_100(
        max(
            _abs_shift_z(corr_xy, baseline_stats.get("corr_xy")),
            _abs_shift_z(corr_xz, baseline_stats.get("corr_xz")),
            _abs_shift_z(corr_yz, baseline_stats.get("corr_yz")),
        )
    )
    robust_damping = _z_to_100((
        _positive_z(az_cv, baseline_stats.get("az_cv"))
        + _positive_z(az_jerk_rms, baseline_stats.get("az_jerk_rms"))
    ) / 2.0)
    robust_spiky = _z_to_100(
        0.40 * _positive_z(a_crest, baseline_stats.get("a_crest"))
        + 0.35 * _positive_z(a_kurt, baseline_stats.get("a_kurt"))
        + 0.25 * _positive_z(peak_rate_hz, baseline_stats.get("peak_rate_hz"))
    )
    robust_shared_abnormal = _z_to_100((
        _positive_z(a_rms_ac, baseline_stats.get("a_rms_ac"))
        + _positive_z(a_p2p, baseline_stats.get("a_p2p"))
        + _positive_z(g_std, baseline_stats.get("g_std"))
    ) / 3.0)

    shared_abnormal_score = baseline_weight * robust_shared_abnormal + (1.0 - baseline_weight) * fallback_shared_abnormal
    vertical_component = baseline_weight * robust_vertical + (1.0 - baseline_weight) * fallback_vertical
    directional_component = baseline_weight * robust_directional + (1.0 - baseline_weight) * fallback_directional
    coupling_component = baseline_weight * robust_coupling + (1.0 - baseline_weight) * fallback_coupling
    damping_component = baseline_weight * robust_damping + (1.0 - baseline_weight) * fallback_damping
    spiky_penalty = baseline_weight * robust_spiky + (1.0 - baseline_weight) * fallback_spiky
    energy_component = baseline_weight * _z_to_100(_positive_z(energy_z_over_xy, baseline_stats.get("energy_z_over_xy"))) + (
        1.0 - baseline_weight
    ) * ratio_to_100(energy_z_over_xy, 0.85, 1.85)
    p2p_component = baseline_weight * _z_to_100(_positive_z(az_p2p, baseline_stats.get("az_p2p"))) + (
        1.0 - baseline_weight
    ) * ratio_to_100(az_p2p, 0.038, 0.062)
    cv_component = baseline_weight * _z_to_100(_positive_z(az_cv, baseline_stats.get("az_cv"))) + (
        1.0 - baseline_weight
    ) * ratio_to_100(az_cv, 0.80, 1.60)
    jerk_component = baseline_weight * _z_to_100(_positive_z(az_jerk_rms, baseline_stats.get("az_jerk_rms"))) + (
        1.0 - baseline_weight
    ) * ratio_to_100(az_jerk_rms, 0.014, 0.032)

    rubber_core_values = [
        energy_component,
        p2p_component,
        cv_component,
        jerk_component,
        coupling_component,
    ]
    rubber_hits = _count_hits(rubber_core_values, rule_cfg["feature_hit_min"])
    rubber_strong_hits = _count_hits(rubber_core_values, rule_cfg["feature_strong_min"])
    rubber_specific_score = clamp(sum(rubber_core_values) / len(rubber_core_values), 0.0, 100.0)
    baseline_deviation_score = clamp(shared_abnormal_score, 0.0, 100.0)
    candidate_signal = 0.0
    watch_signal = 0.0
    mode = "support_stiffness_shift"

    gate_rescue_score = rubber_specific_score
    effective_run_score = max(run_state_score, gate_rescue_score)

    gate_mode = "running"
    if effective_run_score < rule_cfg["watch_run_min"]:
        gate_mode = "non_running_suppressed"
    elif effective_run_score < 45.0:
        gate_mode = "weak_running_suppressed"

    candidate_ready = (
        rubber_hits >= rule_cfg["candidate_hit_min"]
        and rubber_strong_hits >= rule_cfg["candidate_strong_min"]
        and effective_run_score >= rule_cfg["candidate_run_min"]
        and spiky_penalty <= rule_cfg["spiky_penalty_max"]
    )
    watch_ready = (
        rubber_hits >= rule_cfg["watch_hit_min"]
        and effective_run_score >= rule_cfg["watch_run_min"]
    )

    if candidate_ready:
        confirm_mode = "candidate_hits_pass"
        candidate_signal = clamp(
            rule_cfg["candidate_score"] + 4.0 * max(0, rubber_strong_hits - rule_cfg["candidate_strong_min"]) + 2.0 * max(0, rubber_hits - rule_cfg["candidate_hit_min"]),
            0.0,
            100.0,
        )
        watch_signal = candidate_signal
        score = candidate_signal
    elif watch_ready:
        confirm_mode = "watch_hits_pass"
        watch_signal = clamp(
            rule_cfg["watch_score"] + 3.0 * max(0, rubber_hits - rule_cfg["watch_hit_min"]) + 2.0 * max(0, rubber_strong_hits - 1),
            rule_cfg["watch_score"],
            rule_cfg["watch_score_cap"],
        )
        score = watch_signal
    else:
        confirm_mode = "suppressed_single_window"
        watch_signal = 20.0 + 6.0 * rubber_hits + 3.0 * rubber_strong_hits
        score = min(watch_signal, 44.0)
    score = clamp(score, 0.0, 100.0)

    reasons = [
        f"mode={mode}",
        f"baseline_mode={baseline_mode}",
        f"baseline_features={baseline_count}",
        f"baseline_weight={baseline_weight:.3f}",
        f"gate={gate_mode}",
        f"confirm={confirm_mode}",
        f"run_state_score={run_state_score:.2f}",
        f"effective_run_score={effective_run_score:.2f}",
        f"gate_rescue_score={gate_rescue_score:.2f}",
        f"candidate_signal={candidate_signal:.2f}",
        f"watch_signal={watch_signal:.2f}",
        f"core_hits={rubber_hits}",
        f"core_strong_hits={rubber_strong_hits}",
        f"component_vertical={vertical_component:.2f}",
        f"component_directional={directional_component:.2f}",
        f"component_coupling={coupling_component:.2f}",
        f"component_damping={damping_component:.2f}",
        f"component_energy={energy_component:.2f}",
        f"component_p2p={p2p_component:.2f}",
        f"component_cv={cv_component:.2f}",
        f"component_jerk={jerk_component:.2f}",
        f"spiky_penalty={spiky_penalty:.2f}",
        f"energy_z_over_xy={energy_z_over_xy:.4f}",
        f"corr_major={corr_major:.4f}",
        f"az_std={az_std:.6f}",
        f"az_p2p={az_p2p:.6f}",
        f"az_cv={az_cv:.4f}",
        f"az_jerk_rms={az_jerk_rms:.6f}",
    ]

    result = build_result(
        fault_type=FAULT_TYPE,
        score=score,
        reasons=reasons,
        features=features,
        min_samples=8,
        penalize_low_fs=False,
    )
    result["detector_family"] = "rubber"
    result["baseline_mode"] = baseline_mode
    result["baseline_deviation_score"] = round(float(baseline_deviation_score), 2)
    result["rubber_specific_score"] = round(float(rubber_specific_score), 2)
    result["shared_abnormal_score"] = round(float(shared_abnormal_score), 2)
    result["vertical_component_score"] = round(float(vertical_component), 2)
    result["directional_component_score"] = round(float(directional_component), 2)
    result["coupling_component_score"] = round(float(coupling_component), 2)
    result["damping_component_score"] = round(float(damping_component), 2)
    result["spiky_penalty_score"] = round(float(spiky_penalty), 2)
    result["run_state_score"] = round(float(run_state_score), 2)
    result["effective_run_score"] = round(float(effective_run_score), 2)
    result["core_hits"] = int(rubber_hits)
    result["core_strong_hits"] = int(rubber_strong_hits)
    result["specialized_ready"] = confirm_mode == "candidate_hits_pass"
    result["type_watch_ready"] = confirm_mode in {"candidate_hits_pass", "watch_hits_pass"}
    return result


if __name__ == "__main__":
    raise SystemExit(run_detector_cli(detect, description="橡胶圈硬化识别（曳引机支撑刚度/阻尼变化代理量）"))
