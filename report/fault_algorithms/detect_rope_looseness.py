"""钢丝绳松动单窗诊断流程。

1. 先从窗口特征中读取摆动、横向失衡、加角耦合、尖峰冲击等核心指标。
2. 如果有健康基线，就用 median + MAD 做 robust 相对评分；没有基线时退回无量纲 fallback 评分。
3. 再做运行态门控：正常情况依赖运行幅值，低幅值但结构证据很强时允许 structural rescue。
4. 最后做高精度优先的单窗确认：证据不足就封顶到 watch，只把高置信窗口直接触发为 rope_looseness。
5. build_result 会继续结合样本数、质量因子等通用规则输出最终结果。
"""

from __future__ import annotations

import math

try:
    from ._base import build_result, clamp, parse_float, ratio_to_100, run_detector_cli
except ImportError:  # pragma: no cover
    from _base import build_result, clamp, parse_float, ratio_to_100, run_detector_cli

FAULT_TYPE = "rope_looseness"

# 优先使用健康基线对特征做 robust 归一化；当缺少基线时，回退到无量纲自归一化公式。
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
)

# 这组阈值服务于“高精度优先”的单窗确认：
# 只有横向失衡、低频摆动、耦合增强、运行态这几类证据同时成立，才允许单窗直接触发。
# 否则最多保留到 watch 分数，避免把正常工况波动直接推成维保告警。
CONSERVATIVE_ALARM_ENABLED = True
CONSERVATIVE_SCORE_CAP = 59.0
SWING_CONFIRM_MIN_SCORE = 68.0
SWING_CONFIRM_MIN_COMPONENT = 36.0
SWING_CONFIRM_MIN_SWAY = 28.0
SWING_CONFIRM_MAX_SPIKY = 58.0
SWING_CONFIRM_MIN_RUN = 42.0
STRUCTURAL_CONFIRM_MIN_SCORE = 66.0
STRUCTURAL_CONFIRM_MIN_COMPONENT = 58.0
STRUCTURAL_CONFIRM_MAX_SPIKY = 42.0
STRUCTURAL_RESCUE_MIN_SCORE = 40.0

EPS = 1e-9


def _to_float(value: object, default: float = 0.0) -> float:
    parsed = parse_float(value)
    return float(parsed if parsed is not None else default)


def _baseline_stats(features: dict) -> dict[str, tuple[float, float]]:
    # baseline 既可能是 {"stats": {...}}，也可能直接是特征统计字典，这里统一解包。
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
        # scale 来自 robust MAD 尺度，必须为正；过小则兜底到极小正数防止除零。
        if med is None or scale is None or scale <= EPS:
            continue
        stats[str(key)] = (float(med), float(max(scale, 1e-6)))
    return stats


def _positive_z(value: float, stat: tuple[float, float] | None) -> float:
    # 只关心“比正常更大”的偏移。比如横向比、耦合、波动幅值升高通常更可疑。
    if stat is None:
        return 0.0
    med, scale = stat
    return max(0.0, (float(value) - med) / max(scale, 1e-6))


def _negative_z(value: float, stat: tuple[float, float] | None) -> float:
    # 只关心“比正常更小”的偏移。这里主要用于低频摇摆指标 zc_rate_hz 的下降。
    if stat is None:
        return 0.0
    med, scale = stat
    return max(0.0, (med - float(value)) / max(scale, 1e-6))


def _z_to_100(z_value: float, softness: float = 2.2) -> float:
    # z-score 不是直接拿来当最终分数，而是压到 0~100，避免某个单一特征极端放大后主导总分。
    z_pos = max(0.0, float(z_value))
    if z_pos <= 0.0:
        return 0.0
    return 100.0 * (1.0 - math.exp(-z_pos / max(0.4, float(softness))))


def _normalize_weight(count: int, total: int) -> float:
    # 基线覆盖特征越多，robust 基线分数权重越高；覆盖不足时自动向 fallback 倾斜。
    if total <= 0:
        return 0.0
    coverage = max(0.0, min(1.0, float(count) / float(total)))
    return 0.82 * coverage


def detect(features: dict) -> dict:
    # 运行态门控改为无量纲比值，避免把某一台梯子的绝对幅值写死。
    a_mean = max(abs(_to_float(features.get("a_mean"), 1.0)), 1e-3)
    g_mean = max(abs(_to_float(features.get("g_mean"), 0.3)), 0.05)
    a_rms_ac = _to_float(features.get("a_rms_ac"))
    a_p2p = _to_float(features.get("a_p2p"))
    a_crest = _to_float(features.get("a_crest"))
    a_kurt = _to_float(features.get("a_kurt"))
    g_std = _to_float(features.get("g_std"))
    zc_rate_hz = _to_float(features.get("zc_rate_hz"))
    peak_rate_hz = _to_float(features.get("peak_rate_hz"))
    lateral_ratio = _to_float(features.get("lateral_ratio"), 1.0)
    ag_corr = _to_float(features.get("ag_corr"))
    gx_ax_corr = _to_float(features.get("gx_ax_corr"))
    gy_ay_corr = _to_float(features.get("gy_ay_corr"))
    corr_major = max(ag_corr, gx_ax_corr, gy_ay_corr)

    # 先判断“这窗像不像有效运行数据”。
    # 这里不直接把低运行态样本剔除，而是给后续总分一个抑制系数，
    # 这样既能保守压低停梯/微动误报，又能给结构型异常留一条救援通道。
    run_a_ratio = a_rms_ac / max(a_mean, EPS)
    run_g_ratio = g_std / max(g_mean, EPS)
    run_state_score = (
        0.55 * ratio_to_100(run_a_ratio, 0.0015, 0.020)
        + 0.25 * ratio_to_100(run_g_ratio, 0.015, 0.350)
        + 0.20 * ratio_to_100(zc_rate_hz, 0.05, 3.00)
    )

    # 回退公式：全用无量纲比值和相关性，尽量减少对单梯绝对幅值的依赖。
    # 适用于没有健康基线的梯子，此时目标不是精确分级，而是保守筛出“像松绳”的模式。
    sway_energy_ratio = (a_p2p / max(a_mean, EPS)) * max(lateral_ratio, 1.0)
    s_lateral_rel = ratio_to_100(lateral_ratio, 1.05, 2.50)
    s_lowfreq_rel = 100.0 - ratio_to_100(zc_rate_hz, 0.80, 6.00)
    s_coupling_rel = ratio_to_100(corr_major, 0.10, 0.70)
    s_sway_rel = ratio_to_100(sway_energy_ratio, 0.020, 0.240)
    p_spiky_rel = (
        0.40 * ratio_to_100(a_crest, 1.8, 4.2)
        + 0.30 * ratio_to_100(a_kurt, 1.0, 5.5)
        + 0.20 * ratio_to_100(peak_rate_hz / max(zc_rate_hz, 0.20), 1.5, 7.0)
        + 0.10 * ratio_to_100(peak_rate_hz, 0.4, 4.0)
    )
    fallback_score = (
        0.33 * s_lateral_rel
        + 0.28 * s_lowfreq_rel
        + 0.23 * s_coupling_rel
        + 0.16 * s_sway_rel
        - 0.35 * p_spiky_rel
    )
    fallback_score = clamp(fallback_score, 0.0, 100.0)

    baseline_stats = _baseline_stats(features)
    baseline_count = len([key for key in ROPE_BASELINE_KEYS if key in baseline_stats])
    baseline_weight = _normalize_weight(baseline_count, len(ROPE_BASELINE_KEYS))
    baseline_mode = "robust_baseline" if baseline_weight > 0.0 else "self_normalized_fallback"

    # robust 通道的物理含义：
    # - lateral: 横向失衡是否高于健康状态
    # - lowfreq: 摆动频率是否变慢，更像松绳后的低频摇摆
    # - amp: 动态包络/角速度波动是否高于正常
    # - coupling: 加速度与角速度耦合是否增强
    # - spiky: 是否更像冲击/敲击，而不是松绳摆振
    robust_lateral = _z_to_100(_positive_z(lateral_ratio, baseline_stats.get("lateral_ratio")))
    robust_lowfreq = _z_to_100(_negative_z(zc_rate_hz, baseline_stats.get("zc_rate_hz")))
    robust_amp = _z_to_100(
        max(
            _positive_z(a_rms_ac, baseline_stats.get("a_rms_ac")),
            _positive_z(a_p2p, baseline_stats.get("a_p2p")),
            0.65 * _positive_z(g_std, baseline_stats.get("g_std")),
        )
    )
    robust_coupling = _z_to_100(
        max(
            _positive_z(ag_corr, baseline_stats.get("ag_corr")),
            _positive_z(gx_ax_corr, baseline_stats.get("gx_ax_corr")),
            _positive_z(gy_ay_corr, baseline_stats.get("gy_ay_corr")),
        )
    )
    robust_spiky = _z_to_100(
        0.45 * _positive_z(a_crest, baseline_stats.get("a_crest"))
        + 0.35 * _positive_z(a_kurt, baseline_stats.get("a_kurt"))
        + 0.20 * _positive_z(peak_rate_hz, baseline_stats.get("peak_rate_hz"))
    )
    robust_score = (
        0.30 * robust_lateral
        + 0.24 * robust_lowfreq
        + 0.24 * robust_amp
        + 0.22 * robust_coupling
        - 0.32 * robust_spiky
    )
    robust_score = clamp(robust_score, 0.0, 100.0)

    # 最终单窗分数是“robust 基线分数 + fallback 分数”的加权混合：
    # 基线越完整，越相信单梯历史；基线越弱，越退回到通用无量纲规则。
    score = baseline_weight * robust_score + (1.0 - baseline_weight) * fallback_score
    score = clamp(score, 0.0, 100.0)
    mode = "robust_relative_sway"

    # 组件证据统一到同一尺度，用于后续保守确认。
    lateral_component = baseline_weight * robust_lateral + (1.0 - baseline_weight) * s_lateral_rel
    lowfreq_component = baseline_weight * robust_lowfreq + (1.0 - baseline_weight) * s_lowfreq_rel
    coupling_component = baseline_weight * robust_coupling + (1.0 - baseline_weight) * s_coupling_rel
    sway_component = baseline_weight * robust_amp + (1.0 - baseline_weight) * s_sway_rel
    spiky_penalty = baseline_weight * robust_spiky + (1.0 - baseline_weight) * p_spiky_rel
    structural_component = 0.45 * lateral_component + 0.35 * coupling_component + 0.20 * lowfreq_component
    gate_rescue_score = structural_component - 0.35 * spiky_penalty

    # 运行态门控分两路：
    # - 正常情况靠 run_state_score 判断这窗是不是有效运行窗
    # - 如果幅值不大，但结构型证据很强，则允许 structural rescue 兜底
    # 这是为了减少“低振幅松绳窗”被误压成正常。
    effective_run_score = max(run_state_score, gate_rescue_score)
    gate_mode = "running"
    if effective_run_score < 30.0:
        score *= 0.35
        gate_mode = "non_running_suppressed"
    elif effective_run_score < 45.0:
        score *= 0.70
        gate_mode = "weak_running_suppressed"
    elif run_state_score < 30.0 and gate_rescue_score >= STRUCTURAL_RESCUE_MIN_SCORE:
        gate_mode = "structural_rescue"
    score = clamp(score, 0.0, 100.0)

    confirm_mode = "conservative_disabled"
    if CONSERVATIVE_ALARM_ENABLED:
        # swing 通道偏向传统“摆振型松绳”：
        # 横向、低频、耦合、摆动幅值都要到位，且不能太像尖峰冲击。
        swing_single_window_pass = (
            score >= SWING_CONFIRM_MIN_SCORE
            and lateral_component >= SWING_CONFIRM_MIN_COMPONENT
            and lowfreq_component >= SWING_CONFIRM_MIN_COMPONENT
            and coupling_component >= SWING_CONFIRM_MIN_COMPONENT
            and sway_component >= SWING_CONFIRM_MIN_SWAY
            and spiky_penalty <= SWING_CONFIRM_MAX_SPIKY
            and effective_run_score >= SWING_CONFIRM_MIN_RUN
        )
        # structural 通道偏向“低幅值但结构非常像松绳”的情况。
        # 它不要求 sway_component 很高，但要求横向失衡、耦合、低频同时强成立。
        structural_single_window_pass = (
            score >= STRUCTURAL_CONFIRM_MIN_SCORE
            and min(lateral_component, coupling_component, lowfreq_component) >= STRUCTURAL_CONFIRM_MIN_COMPONENT
            and spiky_penalty <= STRUCTURAL_CONFIRM_MAX_SPIKY
            and effective_run_score >= STRUCTURAL_RESCUE_MIN_SCORE
        )
        if swing_single_window_pass:
            confirm_mode = "swing_single_window_pass"
        elif structural_single_window_pass:
            confirm_mode = "structural_single_window_pass"
        else:
            # 单窗证据不足时把分数封顶到 watch 区，交给时间序列连续确认再决定。
            score = min(score, CONSERVATIVE_SCORE_CAP)
            confirm_mode = "suppressed_single_window"
        score = clamp(score, 0.0, 100.0)

    # reasons 里保留中间量，方便现场复盘：
    # 既能看最终分数，也能看是被 gate 压低、被 conservative 封顶，还是哪类组件证据不足。
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
        f"score_fallback={fallback_score:.2f}",
        f"score_robust={robust_score:.2f}",
        f"component_lateral={lateral_component:.2f}",
        f"component_lowfreq={lowfreq_component:.2f}",
        f"component_coupling={coupling_component:.2f}",
        f"component_sway={sway_component:.2f}",
        f"component_structural={structural_component:.2f}",
        f"spiky_penalty={spiky_penalty:.2f}",
        f"corr_major={corr_major:.4f}",
        f"zc_rate_hz={zc_rate_hz:.6f}",
        f"lateral_ratio={lateral_ratio:.4f}",
        f"a_rms_ratio={run_a_ratio:.6f}",
        f"g_std_ratio={run_g_ratio:.6f}",
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
    raise SystemExit(run_detector_cli(detect, description="钢丝绳松动识别（基线相对评分+低频摆动耦合）"))
