from __future__ import annotations

from typing import Any

try:
    from ._base import build_result, clamp, parse_float, ratio_to_100
except ImportError:  # pragma: no cover
    from _base import build_result, clamp, parse_float, ratio_to_100


ROPE_FAULT_TYPE = "rope_looseness"
RUBBER_FAULT_TYPE = "rubber_hardening"
ATTRIBUTION_BASELINE_KEYS: tuple[str, ...] = (
    "corr_xy_abs",
    "corr_xz_abs",
    "energy_x_over_y",
    "az_cv",
    "az_jerk_rms",
)

# 这层不是“异常门”，而是异常后的保守归因。
# 规则只回答：当前异常窗更像 rope 还是 rubber；如果两边证据都不够集中，就保持 unknown。
ROPE_VS_RUBBER_CONFIG = {
    "watch_score": 58.0,
    "candidate_score": 72.0,
    "feature_hit_min": 55.0,
    "feature_strong_min": 72.0,
    "watch_hit_min": 4,
    "candidate_hit_min": 5,
    "candidate_strong_min": 2,
    "watch_margin_min": 12.0,
    "candidate_margin_min": 16.0,
}

ROPE_FEATURE_SPECS: tuple[dict[str, Any], ...] = (
    {"key": "energy_x_over_y", "label": "横向能量比", "direction": "high", "lo": 0.86, "hi": 1.00, "weight": 1.0},
    {"key": "corr_xy_abs", "label": "XY 耦合偏移", "direction": "low", "lo": 0.22, "hi": 0.40, "weight": 1.1},
    {"key": "z_peak_ratio", "label": "竖向谱峰集中度", "direction": "high", "lo": 0.095, "hi": 0.130, "weight": 1.0},
    {"key": "az_cv", "label": "竖向离散度保持", "direction": "high", "lo": 0.88, "hi": 1.00, "weight": 1.0},
    {"key": "az_jerk_rms", "label": "竖向急变保持", "direction": "high", "lo": 0.93, "hi": 1.08, "weight": 0.9},
    {"key": "lateral_ratio", "label": "横向占比", "direction": "low", "lo": 1.10, "hi": 1.35, "weight": 0.9},
    {"key": "lat_dom_freq_hz", "label": "横向主频", "direction": "low", "lo": 1.80, "hi": 3.20, "weight": 0.8},
)

RUBBER_FEATURE_SPECS: tuple[dict[str, Any], ...] = (
    {"key": "corr_xy_abs", "label": "XY 耦合偏移", "direction": "high", "lo": 0.28, "hi": 0.50, "weight": 1.1},
    {"key": "corr_xz_abs", "label": "XZ 耦合偏移", "direction": "high", "lo": 0.16, "hi": 0.28, "weight": 1.0},
    {"key": "a_pca_primary_ratio", "label": "主方向能量占比", "direction": "high", "lo": 0.53, "hi": 0.66, "weight": 1.0},
    {"key": "energy_x_over_y", "label": "横向能量比", "direction": "low", "lo": 0.78, "hi": 0.92, "weight": 1.0},
    {"key": "lateral_ratio", "label": "横向占比", "direction": "high", "lo": 1.15, "hi": 1.45, "weight": 0.9},
    {"key": "lat_dom_freq_hz", "label": "横向主频", "direction": "high", "lo": 2.30, "hi": 4.80, "weight": 0.9},
    {"key": "z_peak_ratio", "label": "竖向谱峰集中度", "direction": "low", "lo": 0.085, "hi": 0.110, "weight": 0.8},
)
BRANCH_BASELINE_RELATIVE_WEIGHT = 0.65
BRANCH_TEMPLATE_WEIGHT = 0.35
BRANCH_BASELINE_SOFTNESS = 2.0
EPS = 1e-9


def _to_float(value: Any, default: float = 0.0) -> float:
    parsed = parse_float(value)
    return float(parsed if parsed is not None else default)


def _score_high(value: float, lo: float, hi: float) -> float:
    return ratio_to_100(float(value), float(lo), float(hi))


def _score_low(value: float, lo: float, hi: float) -> float:
    return 100.0 - ratio_to_100(float(value), float(lo), float(hi))


def _feature_value(features: dict[str, Any], key: str) -> float:
    if key == "corr_xy_abs":
        return abs(_to_float(features.get("corr_xy")))
    if key == "corr_xz_abs":
        return abs(_to_float(features.get("corr_xz")))
    return _to_float(features.get(key))


def _baseline_stat(baseline: dict[str, Any] | None, key: str) -> tuple[float, float] | None:
    if not isinstance(baseline, dict):
        return None
    stats = baseline.get("stats") if isinstance(baseline.get("stats"), dict) else baseline
    if not isinstance(stats, dict):
        return None
    item = stats.get(key)
    if not isinstance(item, dict):
        return None
    median = parse_float(item.get("median"))
    scale = parse_float(item.get("scale"))
    if median is None or scale is None or float(scale) <= EPS:
        return None
    return float(median), float(scale)


def _relative_score(value: float, *, median: float, scale: float, direction: str, lo: float, hi: float) -> float:
    width = max(abs(float(hi) - float(lo)), EPS)
    effective_scale = max(float(scale), width * 0.18, abs(float(median)) * 0.12, 1e-6)
    if str(direction) == "low":
        delta = max(0.0, float(median) - float(value))
    else:
        delta = max(0.0, float(value) - float(median))
    z_value = delta / effective_scale
    return 100.0 * (1.0 - pow(2.718281828459045, -z_value / max(0.35, BRANCH_BASELINE_SOFTNESS)))


def _branch_components(
    features: dict[str, Any],
    specs: tuple[dict[str, Any], ...],
    *,
    baseline: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    for spec in specs:
        value = _feature_value(features, str(spec["key"]))
        direction = str(spec.get("direction", "high"))
        lo = _to_float(spec.get("lo"))
        hi = _to_float(spec.get("hi"))
        if direction == "low":
            template_score = _score_low(value, lo, hi)
        else:
            template_score = _score_high(value, lo, hi)
        baseline_key = str(spec.get("baseline_key", spec["key"]))
        stat = _baseline_stat(baseline, baseline_key)
        relative_score = None
        score = float(clamp(template_score, 0.0, 100.0))
        if stat is not None:
            relative_score = _relative_score(
                value,
                median=float(stat[0]),
                scale=float(stat[1]),
                direction=direction,
                lo=lo,
                hi=hi,
            )
            # 归因优先参考“相对本梯健康基线的偏移”，模板区间只保留为弱先验，减少跨梯失灵。
            score = (
                BRANCH_BASELINE_RELATIVE_WEIGHT * float(relative_score)
                + BRANCH_TEMPLATE_WEIGHT * float(template_score)
            )
        components.append(
            {
                "key": str(spec["key"]),
                "label": str(spec.get("label", spec["key"])),
                "value": float(value),
                "score": float(clamp(score, 0.0, 100.0)),
                "template_score": float(clamp(template_score, 0.0, 100.0)),
                "relative_score": None if relative_score is None else float(clamp(relative_score, 0.0, 100.0)),
                "weight": float(spec.get("weight", 1.0)),
            }
        )
    return components


def _branch_score(components: list[dict[str, Any]]) -> tuple[float, int, int]:
    if not components:
        return 0.0, 0, 0
    total_weight = sum(max(0.0, float(item.get("weight", 1.0))) for item in components)
    if total_weight <= 0.0:
        total_weight = float(len(components))
    weighted = sum(float(item.get("score", 0.0)) * max(0.0, float(item.get("weight", 1.0))) for item in components)
    score = float(weighted / max(total_weight, 1e-6))
    hit_min = float(ROPE_VS_RUBBER_CONFIG["feature_hit_min"])
    strong_min = float(ROPE_VS_RUBBER_CONFIG["feature_strong_min"])
    hits = sum(1 for item in components if float(item.get("score", 0.0)) >= hit_min)
    strong_hits = sum(1 for item in components if float(item.get("score", 0.0)) >= strong_min)
    return score, hits, strong_hits


def _decision_ready(score: float, hits: int, strong_hits: int, margin: float) -> tuple[bool, bool]:
    cfg = ROPE_VS_RUBBER_CONFIG
    watch_ready = (
        hits >= int(cfg["watch_hit_min"])
        and score >= float(cfg["watch_score"])
        and margin >= float(cfg["watch_margin_min"])
    )
    candidate_ready = (
        hits >= int(cfg["candidate_hit_min"])
        and strong_hits >= int(cfg["candidate_strong_min"])
        and score >= float(cfg["candidate_score"])
        and margin >= float(cfg["candidate_margin_min"])
    )
    return bool(watch_ready), bool(candidate_ready)


def _branch_payload(
    *,
    fault_type: str,
    detector_family: str,
    features: dict[str, Any],
    components: list[dict[str, Any]],
    score: float,
    other_score: float,
    watch_ready: bool,
    candidate_ready: bool,
) -> dict[str, Any]:
    reasons = [
        "mode=rope_vs_rubber_v1",
        f"branch={detector_family}",
        f"branch_score={score:.2f}",
        f"other_score={other_score:.2f}",
        f"margin={score - other_score:.2f}",
        f"type_watch_ready={'true' if watch_ready else 'false'}",
        f"type_candidate_ready={'true' if candidate_ready else 'false'}",
    ]
    for item in sorted(components, key=lambda row: float(row.get("score", 0.0)), reverse=True)[:4]:
        reasons.append(f"component_{item['key']}={float(item.get('score', 0.0)):.2f}")
    payload = build_result(
        fault_type=fault_type,
        score=score,
        reasons=reasons,
        features=features,
        min_samples=8,
        penalize_low_fs=False,
    )
    payload["detector_family"] = detector_family
    payload["branch_score"] = round(float(score), 2)
    payload["other_branch_score"] = round(float(other_score), 2)
    payload["attribution_margin"] = round(float(score - other_score), 2)
    payload["feature_hits"] = sum(
        1 for item in components if float(item.get("score", 0.0)) >= float(ROPE_VS_RUBBER_CONFIG["feature_hit_min"])
    )
    payload["feature_strong_hits"] = sum(
        1 for item in components if float(item.get("score", 0.0)) >= float(ROPE_VS_RUBBER_CONFIG["feature_strong_min"])
    )
    payload["type_watch_ready"] = bool(watch_ready)
    payload["type_candidate_ready"] = bool(candidate_ready)
    payload["component_scores"] = {
        str(item["key"]): round(float(item.get("score", 0.0)), 2)
        for item in components
    }
    return payload


def _selected_issue(
    *,
    branch_payload: dict[str, Any],
    system_abnormality: dict[str, Any],
    abnormal_status: str,
) -> dict[str, Any]:
    floor = 60.0 if abnormal_status == "candidate_faults" else 45.0
    cap = 100.0 if abnormal_status == "candidate_faults" else 59.0
    system_score = max(floor, _to_float(system_abnormality.get("score"), 0.0))
    branch_score = max(floor, _to_float(branch_payload.get("score"), 0.0))
    score = min(cap, min(system_score, branch_score))
    return {
        "fault_type": str(branch_payload.get("fault_type", "unknown")),
        "score": round(float(score), 2),
        "level": "warning" if abnormal_status == "candidate_faults" else "watch",
        "triggered": abnormal_status == "candidate_faults",
        "quality_factor": round(_to_float(branch_payload.get("quality_factor"), 1.0), 3),
        "reasons": list(branch_payload.get("reasons", [])) if isinstance(branch_payload.get("reasons"), list) else [],
        "feature_snapshot": dict(branch_payload.get("feature_snapshot", {})) if isinstance(branch_payload.get("feature_snapshot"), dict) else {},
        "screening": "high_confidence" if abnormal_status == "candidate_faults" else "watch",
        "attribution_margin": round(_to_float(branch_payload.get("attribution_margin"), 0.0), 2),
    }


def attribute(
    features: dict[str, Any],
    *,
    system_abnormality: dict[str, Any] | None = None,
    baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    abnormality = system_abnormality if isinstance(system_abnormality, dict) else {}
    abnormal_status = str(abnormality.get("status", "normal")).strip().lower()
    sampling_ok = bool(features.get("sampling_ok", features.get("sampling_ok_40hz", False)))
    if abnormal_status not in {"watch_only", "candidate_faults"} or not sampling_ok:
        return {
            "rope_primary": {},
            "rubber_primary": {},
            "selected_issue": {},
            "auxiliary_results": [],
        }

    rope_components = _branch_components(features, ROPE_FEATURE_SPECS, baseline=baseline)
    rubber_components = _branch_components(features, RUBBER_FEATURE_SPECS, baseline=baseline)
    rope_score, rope_hits, rope_strong_hits = _branch_score(rope_components)
    rubber_score, rubber_hits, rubber_strong_hits = _branch_score(rubber_components)
    rope_margin = float(rope_score - rubber_score)
    rubber_margin = float(rubber_score - rope_score)
    rope_watch_ready, rope_candidate_ready = _decision_ready(rope_score, rope_hits, rope_strong_hits, rope_margin)
    rubber_watch_ready, rubber_candidate_ready = _decision_ready(rubber_score, rubber_hits, rubber_strong_hits, rubber_margin)

    rope_primary = _branch_payload(
        fault_type=ROPE_FAULT_TYPE,
        detector_family="rope",
        features=features,
        components=rope_components,
        score=rope_score,
        other_score=rubber_score,
        watch_ready=rope_watch_ready,
        candidate_ready=rope_candidate_ready,
    )
    rubber_primary = _branch_payload(
        fault_type=RUBBER_FAULT_TYPE,
        detector_family="rubber",
        features=features,
        components=rubber_components,
        score=rubber_score,
        other_score=rope_score,
        watch_ready=rubber_watch_ready,
        candidate_ready=rubber_candidate_ready,
    )

    selected_issue: dict[str, Any] = {}
    if abnormal_status == "candidate_faults":
        rope_ready = rope_candidate_ready or rope_watch_ready
        rubber_ready = rubber_candidate_ready or rubber_watch_ready
        if rope_candidate_ready and not rubber_candidate_ready:
            selected_issue = _selected_issue(branch_payload=rope_primary, system_abnormality=abnormality, abnormal_status=abnormal_status)
        elif rubber_candidate_ready and not rope_candidate_ready:
            selected_issue = _selected_issue(branch_payload=rubber_primary, system_abnormality=abnormality, abnormal_status=abnormal_status)
        elif rope_ready and not rubber_ready and rope_margin >= float(ROPE_VS_RUBBER_CONFIG["candidate_margin_min"]):
            selected_issue = _selected_issue(branch_payload=rope_primary, system_abnormality=abnormality, abnormal_status=abnormal_status)
        elif rubber_ready and not rope_ready and rubber_margin >= float(ROPE_VS_RUBBER_CONFIG["candidate_margin_min"]):
            selected_issue = _selected_issue(branch_payload=rubber_primary, system_abnormality=abnormality, abnormal_status=abnormal_status)
    else:
        rope_ready = rope_candidate_ready or rope_watch_ready
        rubber_ready = rubber_candidate_ready or rubber_watch_ready
        if rope_ready and not rubber_ready:
            selected_issue = _selected_issue(branch_payload=rope_primary, system_abnormality=abnormality, abnormal_status=abnormal_status)
        elif rubber_ready and not rope_ready:
            selected_issue = _selected_issue(branch_payload=rubber_primary, system_abnormality=abnormality, abnormal_status=abnormal_status)

    return {
        "rope_primary": rope_primary,
        "rubber_primary": rubber_primary,
        "selected_issue": selected_issue,
        "auxiliary_results": [],
    }
