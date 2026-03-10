"""橡胶圈硬化单窗诊断。

适用前提是传感器安装在曳引机本体附近。这里不直接把“总振动变大”当作硬化，
而是优先看支撑边界条件变化带来的几个代理现象：

1. 竖向响应增强：Z 向波动、峰峰值、交流 RMS 更偏离健康状态。
2. 阻尼退化代理量增强：Z 向离散点的 jerk / qspread / cv 变大。
3. 轴间耦合结构变化：corr_xy / corr_xz / corr_yz 相对健康基线发生明显偏移。
4. 方向性能量再分配：energy_z_over_xy 上升，说明竖向传递更直接。
5. 明显冲击尖峰要被排除，避免把碰撞、急停、敲击误判成硬化。
"""

from __future__ import annotations

import math

try:
    from ._base import build_result, clamp, parse_float, ratio_to_100, run_detector_cli
except ImportError:  # pragma: no cover
    from _base import build_result, clamp, parse_float, ratio_to_100, run_detector_cli


FAULT_TYPE = "rubber_hardening"

RUBBER_BASELINE_KEYS = (
    "az_std",
    "az_rms_ac",
    "az_p2p",
    "az_cv",
    "az_jerk_rms",
    "az_qspread",
    "corr_xy",
    "corr_xz",
    "corr_yz",
    "energy_z_over_xy",
    "mag_cv",
    "mag_std",
    "a_crest",
    "a_kurt",
    "peak_rate_hz",
)

CONSERVATIVE_ALARM_ENABLED = True
CONSERVATIVE_SCORE_CAP = 59.0
BASELINE_CONFIRM_MIN_SCORE = 64.0
BASELINE_CONFIRM_MIN_VERTICAL = 46.0
BASELINE_CONFIRM_MIN_DIRECTIONAL = 40.0
BASELINE_CONFIRM_MIN_COUPLING = 44.0
BASELINE_CONFIRM_MIN_DAMPING = 36.0
BASELINE_CONFIRM_MAX_SPIKY = 44.0
FALLBACK_CONFIRM_MIN_SCORE = 72.0
FALLBACK_CONFIRM_MIN_VERTICAL = 56.0
FALLBACK_CONFIRM_MIN_COUPLING = 48.0
FALLBACK_CONFIRM_MIN_DAMPING = 42.0
FALLBACK_CONFIRM_MAX_SPIKY = 34.0
RUN_GATE_RESCUE_MIN = 35.0

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


def detect(features: dict) -> dict:
    a_mean = max(abs(_to_float(features.get("a_mean"), 1.0)), 1e-3)
    g_mean = max(abs(_to_float(features.get("g_mean"), 0.3)), 0.05)
    a_rms_ac = _to_float(features.get("a_rms_ac"))
    a_crest = _to_float(features.get("a_crest"))
    a_kurt = _to_float(features.get("a_kurt"))
    g_std = _to_float(features.get("g_std"))
    peak_rate_hz = _to_float(features.get("peak_rate_hz"))

    ax_p2p = _to_float(features.get("ax_p2p"))
    ay_p2p = _to_float(features.get("ay_p2p"))
    az_p2p = _to_float(features.get("az_p2p"))
    ax_rms_ac = _to_float(features.get("ax_rms_ac"))
    ay_rms_ac = _to_float(features.get("ay_rms_ac"))
    az_rms_ac = _to_float(features.get("az_rms_ac"))
    az_std = _to_float(features.get("az_std"))
    az_cv = _to_float(features.get("az_cv"))
    az_jerk_rms = _to_float(features.get("az_jerk_rms"))
    az_qspread = _to_float(features.get("az_qspread"))
    mag_std = _to_float(features.get("mag_std"))
    mag_cv = _to_float(features.get("mag_cv"))
    corr_xy = _to_float(features.get("corr_xy"))
    corr_xz = _to_float(features.get("corr_xz"))
    corr_yz = _to_float(features.get("corr_yz"))
    energy_z_over_xy = _to_float(features.get("energy_z_over_xy"), 1.0)
    energy_x_over_y = _to_float(features.get("energy_x_over_y"), 1.0)

    vertical_rms_ratio = az_rms_ac / max(0.5 * (ax_rms_ac + ay_rms_ac), EPS)
    vertical_p2p_ratio = az_p2p / max(0.5 * (ax_p2p + ay_p2p), EPS)
    damping_ratio = az_jerk_rms / max(az_rms_ac, EPS)
    corr_major = max(abs(corr_xy), abs(corr_xz), abs(corr_yz))

    run_a_ratio = a_rms_ac / max(a_mean, EPS)
    run_g_ratio = g_std / max(g_mean, EPS)
    run_state_score = (
        0.55 * ratio_to_100(run_a_ratio, 0.0010, 0.012)
        + 0.45 * ratio_to_100(run_g_ratio, 0.010, 0.220)
    )

    fallback_vertical = max(
        ratio_to_100(az_std, 0.009, 0.018),
        ratio_to_100(az_p2p, 0.038, 0.062),
        ratio_to_100(vertical_rms_ratio, 0.85, 2.10),
        ratio_to_100(energy_z_over_xy, 0.85, 1.85),
    )
    fallback_directional = (
        0.60 * ratio_to_100(energy_z_over_xy, 0.85, 1.85)
        + 0.40 * (100.0 - ratio_to_100(mag_cv, 0.012, 0.032))
    )
    fallback_coupling = (
        0.70 * ratio_to_100(corr_major, 0.20, 0.90)
        + 0.30 * ratio_to_100(abs(energy_x_over_y - 1.0), 0.08, 0.90)
    )
    fallback_damping = max(
        0.55 * ratio_to_100(az_cv, 0.80, 1.60) + 0.45 * ratio_to_100(az_jerk_rms, 0.014, 0.032),
        ratio_to_100(damping_ratio, 0.85, 2.50),
        ratio_to_100(az_qspread, 0.024, 0.052),
    )
    fallback_spiky = (
        0.45 * ratio_to_100(a_crest, 1.8, 4.2)
        + 0.35 * ratio_to_100(a_kurt, 1.0, 6.0)
        + 0.20 * ratio_to_100(peak_rate_hz, 0.40, 3.50)
    )
    fallback_score = (
        0.34 * fallback_vertical
        + 0.22 * fallback_directional
        + 0.24 * fallback_coupling
        + 0.20 * fallback_damping
        - 0.32 * fallback_spiky
    )
    fallback_score = clamp(fallback_score, 0.0, 100.0)

    baseline_stats = _baseline_stats(features)
    baseline_count = len([key for key in RUBBER_BASELINE_KEYS if key in baseline_stats])
    baseline_weight = _normalize_weight(baseline_count, len(RUBBER_BASELINE_KEYS))
    baseline_mode = "robust_baseline" if baseline_weight > 0.0 else "self_normalized_fallback"

    robust_vertical = _z_to_100(
        max(
            _positive_z(az_std, baseline_stats.get("az_std")),
            _positive_z(az_rms_ac, baseline_stats.get("az_rms_ac")),
            _positive_z(az_p2p, baseline_stats.get("az_p2p")),
            0.70 * _positive_z(az_cv, baseline_stats.get("az_cv")),
            0.55 * _positive_z(energy_z_over_xy, baseline_stats.get("energy_z_over_xy")),
        )
    )
    robust_directional = _z_to_100(
        max(
            _positive_z(energy_z_over_xy, baseline_stats.get("energy_z_over_xy")),
            0.70 * _negative_z(mag_cv, baseline_stats.get("mag_cv")),
            0.45 * _positive_z(mag_std, baseline_stats.get("mag_std")),
        )
    )
    robust_coupling = _z_to_100(
        max(
            _abs_shift_z(corr_xy, baseline_stats.get("corr_xy")),
            _abs_shift_z(corr_xz, baseline_stats.get("corr_xz")),
            _abs_shift_z(corr_yz, baseline_stats.get("corr_yz")),
        )
    )
    robust_damping = _z_to_100(
        max(
            _positive_z(az_cv, baseline_stats.get("az_cv")),
            _positive_z(az_jerk_rms, baseline_stats.get("az_jerk_rms")),
            _positive_z(az_qspread, baseline_stats.get("az_qspread")),
            0.45 * _negative_z(mag_cv, baseline_stats.get("mag_cv")),
        )
    )
    robust_spiky = _z_to_100(
        0.45 * _positive_z(a_crest, baseline_stats.get("a_crest"))
        + 0.35 * _positive_z(a_kurt, baseline_stats.get("a_kurt"))
        + 0.20 * _positive_z(peak_rate_hz, baseline_stats.get("peak_rate_hz"))
    )
    robust_score = (
        0.32 * robust_vertical
        + 0.22 * robust_directional
        + 0.26 * robust_coupling
        + 0.20 * robust_damping
        - 0.30 * robust_spiky
    )
    robust_score = clamp(robust_score, 0.0, 100.0)

    score = baseline_weight * robust_score + (1.0 - baseline_weight) * fallback_score
    score = clamp(score, 0.0, 100.0)
    mode = "support_stiffness_shift"

    vertical_component = baseline_weight * robust_vertical + (1.0 - baseline_weight) * fallback_vertical
    directional_component = baseline_weight * robust_directional + (1.0 - baseline_weight) * fallback_directional
    coupling_component = baseline_weight * robust_coupling + (1.0 - baseline_weight) * fallback_coupling
    damping_component = baseline_weight * robust_damping + (1.0 - baseline_weight) * fallback_damping
    spiky_penalty = baseline_weight * robust_spiky + (1.0 - baseline_weight) * fallback_spiky

    gate_rescue_score = 0.45 * vertical_component + 0.30 * coupling_component + 0.25 * damping_component - 0.30 * spiky_penalty
    effective_run_score = max(run_state_score, gate_rescue_score)
    absolute_signature_votes = sum(
        1
        for passed in (
            az_p2p >= 0.050,
            az_std >= 0.011,
            az_cv >= 1.15,
            energy_z_over_xy >= 1.20,
            az_qspread >= 0.030,
        )
        if passed
    )

    gate_mode = "running"
    if effective_run_score < 25.0:
        score *= 0.30
        gate_mode = "non_running_suppressed"
    elif effective_run_score < 40.0:
        score *= 0.65
        gate_mode = "weak_running_suppressed"
    elif run_state_score < 25.0 and gate_rescue_score >= RUN_GATE_RESCUE_MIN:
        gate_mode = "structural_rescue"
    score = clamp(score, 0.0, 100.0)

    confirm_mode = "conservative_disabled"
    if CONSERVATIVE_ALARM_ENABLED:
        baseline_relative_pass = (
            baseline_weight >= 0.30
            and score >= BASELINE_CONFIRM_MIN_SCORE
            and vertical_component >= BASELINE_CONFIRM_MIN_VERTICAL
            and directional_component >= BASELINE_CONFIRM_MIN_DIRECTIONAL
            and coupling_component >= BASELINE_CONFIRM_MIN_COUPLING
            and damping_component >= BASELINE_CONFIRM_MIN_DAMPING
            and spiky_penalty <= BASELINE_CONFIRM_MAX_SPIKY
            and effective_run_score >= RUN_GATE_RESCUE_MIN
            and absolute_signature_votes >= 2
        )
        fallback_extreme_pass = (
            score >= FALLBACK_CONFIRM_MIN_SCORE
            and vertical_component >= FALLBACK_CONFIRM_MIN_VERTICAL
            and coupling_component >= FALLBACK_CONFIRM_MIN_COUPLING
            and damping_component >= FALLBACK_CONFIRM_MIN_DAMPING
            and spiky_penalty <= FALLBACK_CONFIRM_MAX_SPIKY
            and effective_run_score >= RUN_GATE_RESCUE_MIN
            and absolute_signature_votes >= 3
        )
        if baseline_relative_pass:
            confirm_mode = "baseline_relative_pass"
        elif fallback_extreme_pass:
            confirm_mode = "fallback_extreme_pass"
        else:
            score = min(score, CONSERVATIVE_SCORE_CAP)
            confirm_mode = "suppressed_single_window"
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
        f"absolute_signature_votes={absolute_signature_votes}",
        f"score_fallback={fallback_score:.2f}",
        f"score_robust={robust_score:.2f}",
        f"component_vertical={vertical_component:.2f}",
        f"component_directional={directional_component:.2f}",
        f"component_coupling={coupling_component:.2f}",
        f"component_damping={damping_component:.2f}",
        f"spiky_penalty={spiky_penalty:.2f}",
        f"energy_z_over_xy={energy_z_over_xy:.4f}",
        f"vertical_rms_ratio={vertical_rms_ratio:.4f}",
        f"vertical_p2p_ratio={vertical_p2p_ratio:.4f}",
        f"damping_ratio={damping_ratio:.4f}",
        f"corr_major={corr_major:.4f}",
        f"az_std={az_std:.6f}",
        f"az_p2p={az_p2p:.6f}",
        f"az_cv={az_cv:.4f}",
        f"az_jerk_rms={az_jerk_rms:.6f}",
        f"mag_cv={mag_cv:.6f}",
    ]

    return build_result(
        fault_type=FAULT_TYPE,
        score=score,
        reasons=reasons,
        features=features,
        min_samples=8,
        penalize_low_fs=False,
    )


if __name__ == "__main__":
    raise SystemExit(run_detector_cli(detect, description="橡胶圈硬化识别（曳引机支撑刚度/阻尼变化代理量）"))
