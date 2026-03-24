"""钢丝绳状态异常单窗诊断。

当前版本收敛成“少量证据值 + hit 数”的规则链，主判依赖这组特征：

1. `A_mag RMS`
2. `0-5Hz` 低频能量
3. `5-20Hz` 高频能量
4. 低/高频 band ratio
5. `A_mag` 过零率 `ZCR`
6. 局部峰值离散度 `peak_std`
7. 三轴加速度 `PCA` 主方向能量占比

当前只保留规则链路，不在钢丝绳专项里保留 centroid 训练、加载或融合逻辑。
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
        baseline_mapping_match,
        build_clean_feature_baseline,
        build_feature_pack,
        build_result,
        clamp,
        feature_context_reasons,
        load_rows,
        parse_float,
        ratio_to_100,
    )
except ImportError:  # pragma: no cover
    from _base import (
        baseline_mapping_match,
        build_clean_feature_baseline,
        build_feature_pack,
        build_result,
        clamp,
        feature_context_reasons,
        load_rows,
        parse_float,
        ratio_to_100,
    )


FAULT_TYPE = "rope_tension_abnormal"

ROPE_BASELINE_KEYS = (
    "a_rms_ac",
    "a_band_0_5_energy",
    "a_band_5_20_energy",
    "a_band_log_ratio_0_5_over_5_20",
    "a_zcr_hz",
    "a_peak_std",
    "a_pca_primary_ratio",
)

ROPE_RULE_CONFIG = {
    "watch_score": 52.0,
    "candidate_score": 72.0,
    "feature_hit_min": 48.0,
    "feature_strong_min": 63.0,
    "watch_hit_min": 3,
    "candidate_hit_min": 4,
    "candidate_strong_min": 2,
    "watch_run_min": 30.0,
    "candidate_run_min": 35.0,
    "spiky_penalty_max": 58.0,
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
    a_rms_ac = _to_float(features.get("a_rms_ac"))
    a_band_0_5_energy = _to_float(features.get("a_band_0_5_energy"))
    a_band_5_20_energy = _to_float(features.get("a_band_5_20_energy"))
    a_band_0_5_share = _to_float(features.get("a_band_0_5_share"))
    a_band_5_20_share = _to_float(features.get("a_band_5_20_share"))
    a_band_log_ratio = _to_float(features.get("a_band_log_ratio_0_5_over_5_20"))
    a_zcr_hz = _to_float(features.get("a_zcr_hz"))
    a_peak_std = _to_float(features.get("a_peak_std"))
    a_pca_primary_ratio = _to_float(features.get("a_pca_primary_ratio"))

    # 这组 fallback 是“没有足够健康基线时”的自归一化经验规则：
    # - rope_rms: 整体振动是否明显起来了
    # - rope_lowband / rope_band_ratio: 能量是否向低频摆动区集中
    # - rope_zcr / rope_peak_regular: 是否更像连续慢摆，而不是快抖或冲击
    # - rope_directional: 三轴能量是否收敛到主方向，避免把全向噪声误判成 rope
    rope_rms = ratio_to_100(a_rms_ac, 0.006, 0.028)
    rope_lowband = (
        0.65 * ratio_to_100(a_band_0_5_share, 0.60, 0.95)
        + 0.35 * ratio_to_100(math.log1p(a_band_0_5_energy), math.log1p(0.05), math.log1p(12.0))
    )
    rope_highband = (
        0.65 * ratio_to_100(a_band_5_20_share, 0.04, 0.28)
        + 0.35 * ratio_to_100(math.log1p(a_band_5_20_energy), math.log1p(1e-5), math.log1p(0.8))
    )
    rope_band_ratio = ratio_to_100(a_band_log_ratio, math.log1p(1.5), math.log1p(120.0))
    rope_zcr = 100.0 - ratio_to_100(a_zcr_hz, 7.0, 18.0)
    rope_peak_regular = 100.0 - ratio_to_100(a_peak_std, 0.0015, 0.018)
    rope_directional = ratio_to_100(a_pca_primary_ratio, 0.48, 0.82)
    shared_abnormal = (
        0.40 * rope_rms
        + 0.30 * rope_lowband
        + 0.20 * rope_band_ratio
        + 0.10 * rope_directional
    )
    confounding_score = 0.70 * rope_highband + 0.30 * (100.0 - rope_peak_regular)
    spiky_penalty = confounding_score

    return {
        "rope_rms": float(rope_rms),
        "rope_lowband": float(rope_lowband),
        "rope_highband": float(rope_highband),
        "rope_band_ratio": float(rope_band_ratio),
        "rope_zcr": float(rope_zcr),
        "rope_peak_regular": float(rope_peak_regular),
        "rope_directional": float(rope_directional),
        "shared_abnormal": float(shared_abnormal),
        "confounding_score": float(confounding_score),
        "spiky_penalty": float(spiky_penalty),
        "a_band_0_5_energy": float(a_band_0_5_energy),
        "a_band_5_20_energy": float(a_band_5_20_energy),
        "a_band_log_ratio": float(a_band_log_ratio),
        "a_zcr_hz": float(a_zcr_hz),
        "a_peak_std": float(a_peak_std),
        "a_pca_primary_ratio": float(a_pca_primary_ratio),
    }


def _branch_robust_components(features: dict[str, Any], baseline_stats: dict[str, tuple[float, float]]) -> dict[str, float]:
    a_rms_ac = _to_float(features.get("a_rms_ac"))
    a_band_0_5_energy = _to_float(features.get("a_band_0_5_energy"))
    a_band_5_20_energy = _to_float(features.get("a_band_5_20_energy"))
    a_band_log_ratio = _to_float(features.get("a_band_log_ratio_0_5_over_5_20"))
    a_zcr_hz = _to_float(features.get("a_zcr_hz"))
    a_peak_std = _to_float(features.get("a_peak_std"))
    a_pca_primary_ratio = _to_float(features.get("a_pca_primary_ratio"))

    # 有基线时，同一组证据统一改成“相对健康状态偏离多少”的 z-score 口径。
    rope_rms = _z_to_100(_positive_z(a_rms_ac, baseline_stats.get("a_rms_ac")))
    rope_lowband = _z_to_100(_positive_z(a_band_0_5_energy, baseline_stats.get("a_band_0_5_energy")))
    rope_highband = _z_to_100(_positive_z(a_band_5_20_energy, baseline_stats.get("a_band_5_20_energy")))
    rope_band_ratio = _z_to_100(_positive_z(a_band_log_ratio, baseline_stats.get("a_band_log_ratio_0_5_over_5_20")))
    rope_zcr = _z_to_100(_negative_z(a_zcr_hz, baseline_stats.get("a_zcr_hz")))
    rope_peak_regular = _z_to_100(_negative_z(a_peak_std, baseline_stats.get("a_peak_std")))
    rope_directional = _z_to_100(_positive_z(a_pca_primary_ratio, baseline_stats.get("a_pca_primary_ratio")))
    shared_abnormal = (
        0.40 * rope_rms
        + 0.30 * rope_lowband
        + 0.20 * rope_band_ratio
        + 0.10 * rope_directional
    )
    confounding_score = 0.70 * rope_highband + 0.30 * (100.0 - rope_peak_regular)
    spiky_penalty = confounding_score

    return {
        "rope_rms": float(rope_rms),
        "rope_lowband": float(rope_lowband),
        "rope_highband": float(rope_highband),
        "rope_band_ratio": float(rope_band_ratio),
        "rope_zcr": float(rope_zcr),
        "rope_peak_regular": float(rope_peak_regular),
        "rope_directional": float(rope_directional),
        "shared_abnormal": float(shared_abnormal),
        "confounding_score": float(confounding_score),
        "spiky_penalty": float(spiky_penalty),
        "a_band_0_5_energy": float(a_band_0_5_energy),
        "a_band_5_20_energy": float(a_band_5_20_energy),
        "a_band_log_ratio": float(a_band_log_ratio),
        "a_zcr_hz": float(a_zcr_hz),
        "a_peak_std": float(a_peak_std),
        "a_pca_primary_ratio": float(a_pca_primary_ratio),
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
    sampling_ok = bool(features.get("sampling_ok", features.get("sampling_ok_40hz", False)))
    baseline_payload = features.get("baseline") if isinstance(features.get("baseline"), dict) else None
    baseline_match = baseline_mapping_match(features, baseline_payload)
    baseline_stats = _baseline_stats(features) if baseline_match is not False else {}
    baseline_count = len([key for key in ROPE_BASELINE_KEYS if key in baseline_stats])
    baseline_weight = _normalize_weight(baseline_count, len(ROPE_BASELINE_KEYS)) if baseline_match is not False else 0.0
    fallback = _branch_fallback_components(features)
    robust = _branch_robust_components(features, baseline_stats) if baseline_stats else {key: 0.0 for key in fallback.keys()}
    mixed = _mix_components(baseline_weight, robust, fallback)

    # run_state_score 只回答“这一窗是否确实处于可解释的运行状态”。
    # 它不直接代表 rope，但能把静止、弱运动、偶发小抖动先压住。
    run_state_score = (
        0.40 * mixed["rope_rms"]
        + 0.25 * mixed["rope_lowband"]
        + 0.20 * mixed["rope_band_ratio"]
        + 0.15 * mixed["rope_directional"]
    )
    # regularity_score 更强调“像不像连续摆动”。
    # 对 rope 来说，低 ZCR + 小 peak_std 往往比单纯高能量更关键。
    regularity_score = 0.55 * mixed["rope_zcr"] + 0.45 * mixed["rope_peak_regular"]
    # loose_score 是钢丝绳主链正证据：
    # 低频集中、低高频比增大、节奏变慢、方向性变强。
    loose_score = (
        mixed["rope_lowband"]
        + mixed["rope_band_ratio"]
        + mixed["rope_zcr"]
        + mixed["rope_directional"]
    ) / 4.0
    # tight_score 在这里更像反证据画像：
    # 高频能量偏大、过零率偏快、方向性不明显时，更接近非 rope 摆动。
    tight_score = (
        mixed["rope_highband"]
        + (100.0 - mixed["rope_zcr"])
        + (100.0 - mixed["rope_directional"])
    ) / 3.0
    dominant_branch = "loose_like" if loose_score >= tight_score else "tight_like"
    rope_specific_score = float(loose_score)
    baseline_deviation_score = float(
        (
            mixed["rope_rms"]
            + mixed["rope_lowband"]
            + mixed["rope_band_ratio"]
            + mixed["rope_zcr"]
            + mixed["rope_directional"]
            + mixed["rope_peak_regular"]
        )
        / 6.0
    )
    # core_values 保留少量强相关证据做 hit-count，
    # 避免重新退回复杂加权总分堆叠。
    core_values = [
        mixed["rope_rms"],
        mixed["rope_lowband"],
        mixed["rope_band_ratio"],
        mixed["rope_zcr"],
        mixed["rope_directional"],
    ]
    core_feature_total = len(core_values)
    core_hits = _count_hits(core_values, rule_cfg["feature_hit_min"])
    core_strong_hits = _count_hits(core_values, rule_cfg["feature_strong_min"])
    candidate_signal = 0.0
    watch_signal = 0.0

    # gate_rescue_score 用于避免“整体运行强度一般，但 rope 画像非常明显”的窗口被过早压掉。
    gate_rescue_score = (
        0.45 * mixed["rope_lowband"]
        + 0.30 * mixed["rope_band_ratio"]
        + 0.15 * mixed["rope_directional"]
        + 0.10 * mixed["rope_rms"]
    )
    effective_run_score = max(run_state_score, gate_rescue_score, regularity_score)
    gate_mode = "running"
    if not sampling_ok:
        gate_mode = "sampling_low_quality"
    elif effective_run_score < rule_cfg["watch_run_min"]:
        gate_mode = "non_running_suppressed"
    elif effective_run_score < 45.0:
        gate_mode = "weak_running_suppressed"

    candidate_allowed = sampling_ok and baseline_weight > 0.0 and baseline_match is not False
    # candidate 要求更严格：
    # 1) 采样质量够
    # 2) 基线可用
    # 3) 多个核心证据同时命中
    # 4) 不能太像高频冲击/噪声
    candidate_ready = (
        candidate_allowed
        and
        core_hits >= rule_cfg["candidate_hit_min"]
        and core_strong_hits >= rule_cfg["candidate_strong_min"]
        and effective_run_score >= rule_cfg["candidate_run_min"]
        and mixed["spiky_penalty"] <= rule_cfg["spiky_penalty_max"]
        and mixed["rope_peak_regular"] >= 35.0
    )
    watch_ready = (
        sampling_ok
        and
        core_hits >= rule_cfg["watch_hit_min"]
        and effective_run_score >= rule_cfg["watch_run_min"]
        and loose_score >= 45.0
    )

    if not sampling_ok:
        confirm_mode = "sampling_low_quality"
        watch_signal = 18.0 + 4.0 * core_hits + 2.0 * core_strong_hits
        rule_score = min(watch_signal, 44.0)
    elif candidate_ready:
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
        "baseline_mode": "mapping_mismatch_fallback"
        if baseline_match is False and baseline_payload is not None
        else ("robust_baseline" if baseline_weight > 0.0 else "self_normalized_fallback"),
        "baseline_match": baseline_match,
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
        "core_feature_total": int(core_feature_total),
        "dominant_branch": dominant_branch,
        "rule_score": float(rule_score),
        "candidate_allowed": bool(candidate_allowed),
        "spectral_snapshot": {
            "lat_dom_freq_hz": round(_to_float(features.get("lat_dom_freq_hz")), 4),
            "lat_peak_ratio": round(_to_float(features.get("lat_peak_ratio")), 4),
            "lat_low_band_ratio": round(_to_float(features.get("lat_low_band_ratio")), 4),
            "z_dom_freq_hz": round(_to_float(features.get("z_dom_freq_hz")), 4),
            "z_peak_ratio": round(_to_float(features.get("z_peak_ratio")), 4),
            "z_low_band_ratio": round(_to_float(features.get("z_low_band_ratio")), 4),
            "a_band_0_5_energy": round(mixed["a_band_0_5_energy"], 6),
            "a_band_5_20_energy": round(mixed["a_band_5_20_energy"], 6),
            "a_band_log_ratio_0_5_over_5_20": round(mixed["a_band_log_ratio"], 6),
            "a_zcr_hz": round(mixed["a_zcr_hz"], 4),
            "a_peak_std": round(mixed["a_peak_std"], 6),
            "a_pca_primary_ratio": round(mixed["a_pca_primary_ratio"], 4),
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
        f"core_feature_total={analysis['core_feature_total']}",
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
        f"candidate_allowed={'true' if analysis['candidate_allowed'] else 'false'}",
        f"component_rope_rms={analysis['mixed']['rope_rms']:.2f}",
        f"component_rope_lowband={analysis['mixed']['rope_lowband']:.2f}",
        f"component_rope_highband={analysis['mixed']['rope_highband']:.2f}",
        f"component_rope_band_ratio={analysis['mixed']['rope_band_ratio']:.2f}",
        f"component_rope_zcr={analysis['mixed']['rope_zcr']:.2f}",
        f"component_rope_peak_regular={analysis['mixed']['rope_peak_regular']:.2f}",
        f"component_rope_directional={analysis['mixed']['rope_directional']:.2f}",
        f"component_shared_abnormal={analysis['mixed']['shared_abnormal']:.2f}",
        f"component_confounding={analysis['mixed']['confounding_score']:.2f}",
        f"spiky_penalty={analysis['mixed']['spiky_penalty']:.2f}",
        f"a_band_0_5_energy={analysis['mixed']['a_band_0_5_energy']:.6f}",
        f"a_band_5_20_energy={analysis['mixed']['a_band_5_20_energy']:.6f}",
        f"a_band_log_ratio_0_5_over_5_20={analysis['mixed']['a_band_log_ratio']:.6f}",
        f"a_zcr_hz={analysis['mixed']['a_zcr_hz']:.4f}",
        f"a_peak_std={analysis['mixed']['a_peak_std']:.6f}",
        f"a_pca_primary_ratio={analysis['mixed']['a_pca_primary_ratio']:.4f}",
    ]
    reasons.extend(feature_context_reasons(features, baseline_match=analysis["baseline_match"]))

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
    result["baseline_match"] = analysis["baseline_match"]
    result["baseline_deviation_score"] = round(float(analysis["baseline_deviation_score"]), 2)
    result["rope_specific_score"] = round(float(analysis["rope_specific_score"]), 2)
    result["shared_abnormal_score"] = round(float(analysis["mixed"]["shared_abnormal"]), 2)
    result["confounding_score"] = round(float(analysis["mixed"]["confounding_score"]), 2)
    result["spiky_penalty_score"] = round(float(analysis["mixed"]["spiky_penalty"]), 2)
    result["run_state_score"] = round(float(analysis["run_state_score"]), 2)
    result["effective_run_score"] = round(float(analysis["effective_run_score"]), 2)
    result["sampling_condition"] = str(features.get("sampling_condition", "unknown"))
    result["axis_mapping_mode"] = str(features.get("axis_mapping_mode", "default"))
    result["axis_mapping_signature"] = str(features.get("axis_mapping_signature", ""))
    result["core_hits"] = int(analysis["core_hits"])
    result["core_strong_hits"] = int(analysis["core_strong_hits"])
    result["core_feature_total"] = int(analysis["core_feature_total"])
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
