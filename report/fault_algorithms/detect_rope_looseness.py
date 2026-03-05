from __future__ import annotations

try:
    from ._base import build_result, clamp, ratio_to_100, run_detector_cli
except ImportError:  # pragma: no cover
    from _base import build_result, clamp, ratio_to_100, run_detector_cli


FAULT_TYPE = "rope_looseness"

# 理论优先口径（仅依赖振动/角速度）：
# - 钢丝绳张力不均/松弛更常表现为“运行中低频摆动 + 横向失衡 + 加角耦合增强”；
# - 单纯“能量变低”并不是稳健的物理判据，因此不再保留旧版的 damped_slack 模式；
# - 对冲击型高尖峰信号做抑制，避免把制动抖动/碰撞误判成松绳。
CONSERVATIVE_ALARM_ENABLED = True
SWING_CONFIRM_MIN_SCORE = 68.0
SWING_CONFIRM_MIN_A_RMS_AC = 0.0055
SWING_CONFIRM_MIN_A_P2P = 0.040
SWING_CONFIRM_MIN_LATERAL_RATIO = 1.40
SWING_CONFIRM_MIN_COUPLING = 0.22
SWING_CONFIRM_MAX_ZC_RATE_HZ = 3.0
CONSERVATIVE_SCORE_CAP = 59.0


def detect(features: dict) -> dict:
    # 运行状态门控：只使用振动传感器可直接观测到的动态量。
    # a_rms_ac/g_std 越高，说明当前越可能处于运行段；zc_rate 过低但非零对应低频摆动。
    a_rms_ac = features.get("a_rms_ac", 0.0)
    a_p2p = features.get("a_p2p", 0.0)
    a_crest = features.get("a_crest", 0.0)
    a_kurt = features.get("a_kurt", 0.0)
    g_std = features.get("g_std", 0.0)
    zc_rate_hz = features.get("zc_rate_hz", 0.0)
    peak_rate_hz = features.get("peak_rate_hz", 0.0)
    lateral_ratio = features.get("lateral_ratio", 1.0)
    ag_corr = features.get("ag_corr", 0.0)
    gx_ax_corr = features.get("gx_ax_corr", 0.0)
    gy_ay_corr = features.get("gy_ay_corr", 0.0)

    run_state_score = (
        0.45 * ratio_to_100(a_rms_ac, 0.003, 0.020)
        + 0.35 * ratio_to_100(g_std, 0.015, 0.120)
        + 0.20 * ratio_to_100(zc_rate_hz, 0.05, 3.00)
    )

    # 理论主判据：低频摆动 + 横向比例升高 + 加速度/角速度耦合增强。
    s_lateral = ratio_to_100(lateral_ratio, 0.75, 2.50)
    s_lowfreq = 100.0 - ratio_to_100(zc_rate_hz, 0.50, 6.00)
    s_envelope = 0.55 * ratio_to_100(a_rms_ac, 0.004, 0.018) + 0.45 * ratio_to_100(a_p2p, 0.020, 0.120)
    corr_major = max(ag_corr, gx_ax_corr, gy_ay_corr)
    s_coupling = ratio_to_100(corr_major, 0.15, 0.75)

    # 冲击抑制：高峰值因子 / 高峭度 / 高频冲击密度更像碰撞或制动问题，而非松绳摆动。
    p_spiky = (
        0.45 * ratio_to_100(a_crest, 1.8, 4.0)
        + 0.35 * ratio_to_100(a_kurt, 1.0, 5.0)
        + 0.20 * ratio_to_100(peak_rate_hz, 0.4, 4.0)
    )

    score = 0.34 * s_lateral + 0.28 * s_lowfreq + 0.22 * s_envelope + 0.16 * s_coupling
    score -= 0.35 * p_spiky
    score = clamp(score, 0.0, 100.0)
    mode = "sway_coupled_slack"

    # 运行门控降权：
    # - <30: 基本非运行，分数压到 35%
    # - 30~50: 弱运行，分数压到 70%
    # 目的是降低停梯/微动窗口误报。
    gate_mode = "running"
    if run_state_score < 30.0:
        score *= 0.35
        gate_mode = "non_running_suppressed"
    elif run_state_score < 50.0:
        score *= 0.70
        gate_mode = "weak_running_suppressed"
    score = clamp(score, 0.0, 100.0)

    # 单窗口保守确认：
    # - 只有低频摆动、横向失衡、动态包络、耦合都同时满足时，才允许单窗口进入告警区；
    # - 其余情况即使分数偏高，也先压到 watch 区间，等待时间序列确认。
    confirm_mode = "conservative_disabled"
    if CONSERVATIVE_ALARM_ENABLED:
        swing_single_window_pass = (
            score >= SWING_CONFIRM_MIN_SCORE
            and lateral_ratio >= SWING_CONFIRM_MIN_LATERAL_RATIO
            and (a_rms_ac >= SWING_CONFIRM_MIN_A_RMS_AC or a_p2p >= SWING_CONFIRM_MIN_A_P2P)
            and corr_major >= SWING_CONFIRM_MIN_COUPLING
            and zc_rate_hz <= SWING_CONFIRM_MAX_ZC_RATE_HZ
        )
        if swing_single_window_pass:
            confirm_mode = "swing_single_window_pass"
        else:
            score = min(score, CONSERVATIVE_SCORE_CAP)
            confirm_mode = "suppressed_single_window"
        score = clamp(score, 0.0, 100.0)

    # reasons 输出核心证据，便于报告直接引用（模式、门控和关键特征值）。
    reasons = [
        f"mode={mode}",
        f"gate={gate_mode}",
        f"confirm={confirm_mode}",
        f"run_state_score={run_state_score:.2f}",
        f"a_rms_ac={a_rms_ac:.6f}",
        f"a_p2p={a_p2p:.6f}",
        f"g_std={g_std:.6f}",
        f"corr_major={corr_major:.4f}",
        f"zc_rate_hz={zc_rate_hz:.6f}",
        f"peak_rate_hz={peak_rate_hz:.6f}",
        f"lateral_ratio={lateral_ratio:.4f}",
        f"spiky_penalty={p_spiky:.2f}",
    ]
    # min_samples=8：窗口有效点太少时自动降可信度；
    # penalize_low_fs=False：该算法容忍低频窗口，不额外按 fs 再降权。
    return build_result(
        fault_type=FAULT_TYPE,
        score=score,
        reasons=reasons,
        features=features,
        min_samples=8,
        penalize_low_fs=False,
    )


if __name__ == "__main__":
    raise SystemExit(run_detector_cli(detect, description="钢丝绳松动识别（低频摆动+幅值变化+加角耦合）"))
