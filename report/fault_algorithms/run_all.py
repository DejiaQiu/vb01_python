from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Callable

try:
    from ._base import TARGET_40HZ_CONFIG, baseline_mapping_match, build_clean_feature_baseline, build_feature_pack, load_rows, parse_float, ratio_to_100
    from .detect_rope_looseness import ROPE_BASELINE_KEYS, detect as detect_rope_looseness
except ImportError:  # pragma: no cover
    from _base import TARGET_40HZ_CONFIG, baseline_mapping_match, build_clean_feature_baseline, build_feature_pack, load_rows, parse_float, ratio_to_100
    from detect_rope_looseness import ROPE_BASELINE_KEYS, detect as detect_rope_looseness


_PATTERN = re.compile(r"vibration_30s_\d{8}_(\d{6})\.csv$")

HIGH_CONFIDENCE_SCORE = 60.0
WATCH_SCORE = 45.0
HIGH_CONFIDENCE_QUALITY = 0.80
WATCH_QUALITY = 0.60
MIN_EFFECTIVE_SAMPLES = int(TARGET_40HZ_CONFIG["min_samples"])
BASELINE_KEYS = tuple(dict.fromkeys(ROPE_BASELINE_KEYS))
PRIMARY_DETECTOR: Callable[[dict[str, Any]], dict[str, Any]] = detect_rope_looseness
AUXILIARY_DETECTORS: list[Callable[[dict[str, Any]], dict[str, Any]]] = []
DETECTORS: list[Callable[[dict[str, Any]], dict[str, Any]]] = [PRIMARY_DETECTOR]
EPS = 1e-9
SYSTEM_GATE_CONFIG = {
    "watch_score": WATCH_SCORE,
    "candidate_score": HIGH_CONFIDENCE_SCORE,
    "watch_quality": WATCH_QUALITY,
    "candidate_quality": HIGH_CONFIDENCE_QUALITY,
    "min_effective_samples": MIN_EFFECTIVE_SAMPLES,
    "feature_hit_min": 45.0,
    "feature_strong_min": 60.0,
    "run_watch_min": 30.0,
    "run_candidate_min": 35.0,
    "run_weak_min": 45.0,
}


def _hhmmss(path: Path) -> str:
    m = _PATTERN.match(path.name)
    return m.group(1) if m else "000000"


def _in_range(hhmmss: str, start_hhmm: str, end_hhmm: str) -> bool:
    hhmm = hhmmss[:4]
    return start_hhmm <= hhmm <= end_hhmm


def _select_files(input_dir: Path, start_hhmm: str, end_hhmm: str) -> list[Path]:
    files = sorted(input_dir.glob("vibration_30s_*.csv"), key=lambda p: _hhmmss(p))
    return [path for path in files if _in_range(_hhmmss(path), start_hhmm, end_hhmm)]


def _load_baseline_json(path: Path) -> dict | None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return payload
    return None


def _build_baseline_from_dir(input_dir: Path, start_hhmm: str, end_hhmm: str) -> dict | None:
    feature_rows = [build_feature_pack(load_rows(path)) for path in _select_files(input_dir, start_hhmm, end_hhmm)]
    if not feature_rows:
        return None
    baseline = build_clean_feature_baseline(feature_rows, BASELINE_KEYS, min_samples=MIN_EFFECTIVE_SAMPLES)
    baseline["source"] = str(input_dir)
    baseline["window"] = {"start_hhmm": start_hhmm, "end_hhmm": end_hhmm}
    return baseline


def _quality(result: dict[str, Any]) -> float:
    return float(result.get("quality_factor", 0.0))


def _score(result: dict[str, Any]) -> float:
    return float(result.get("score", 0.0))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _copy_result(result: dict[str, Any], *, screening: str) -> dict[str, Any]:
    payload = dict(result)
    payload["screening"] = screening
    return payload


def _level_from_score(score: float) -> str:
    if score >= 80.0:
        return "alarm"
    if score >= 60.0:
        return "warning"
    if score >= 35.0:
        return "watch"
    return "normal"


def _baseline_stats(baseline: dict[str, Any] | None) -> dict[str, tuple[float, float]]:
    if not isinstance(baseline, dict):
        return {}
    raw_stats = baseline.get("stats") if isinstance(baseline.get("stats"), dict) else baseline
    stats: dict[str, tuple[float, float]] = {}
    for key, item in raw_stats.items():
        if not isinstance(item, dict):
            continue
        median = parse_float(item.get("median"))
        scale = parse_float(item.get("scale"))
        if median is None or scale is None or scale <= EPS:
            continue
        stats[str(key)] = (float(median), float(max(scale, 1e-6)))
    return stats


def _positive_z(value: float, stat: tuple[float, float] | None) -> float:
    if stat is None:
        return 0.0
    median, scale = stat
    return max(0.0, (float(value) - median) / max(scale, 1e-6))


def _z_to_100(z_value: float, softness: float = 2.0) -> float:
    z_pos = max(0.0, float(z_value))
    if z_pos <= 0.0:
        return 0.0
    return 100.0 * (1.0 - pow(2.718281828459045, -z_pos / max(0.35, float(softness))))


def _normalize_weight(count: int, total: int) -> float:
    if total <= 0:
        return 0.0
    coverage = max(0.0, min(1.0, float(count) / float(total)))
    return 0.85 * coverage


def _count_hits(values: list[float], min_score: float) -> int:
    return sum(1 for value in values if float(value) >= float(min_score))


def _to_float(value: Any, default: float = 0.0) -> float:
    parsed = parse_float(value)
    return float(parsed if parsed is not None else default)


def _system_abnormality(features: dict[str, Any], baseline: dict[str, Any] | None) -> dict[str, Any]:
    gate_cfg = SYSTEM_GATE_CONFIG
    sampling_ok = bool(features.get("sampling_ok_40hz", False))
    sampling_condition = str(features.get("sampling_condition", "unknown"))
    a_mean = max(abs(_to_float(features.get("a_mean"), 1.0)), 1e-3)
    g_mean = max(abs(_to_float(features.get("g_mean"), 0.3)), 0.05)
    a_rms_ac = _to_float(features.get("a_rms_ac"))
    a_p2p = _to_float(features.get("a_p2p"))
    g_std = _to_float(features.get("g_std"))
    lat_peak_ratio = _to_float(features.get("lat_peak_ratio"))
    z_peak_ratio = _to_float(features.get("z_peak_ratio"))

    fallback_a_rms = ratio_to_100(a_rms_ac / max(a_mean, EPS), 0.0015, 0.020)
    fallback_a_p2p = ratio_to_100(a_p2p / max(a_mean, EPS), 0.020, 0.180)
    fallback_g_std = ratio_to_100(g_std / max(g_mean, EPS), 0.015, 0.320)
    baseline_match = baseline_mapping_match(features, baseline)
    baseline_stats = _baseline_stats(baseline) if baseline_match is not False else {}
    baseline_count = len([key for key in ("a_rms_ac", "a_p2p", "g_std") if key in baseline_stats])
    baseline_weight = _normalize_weight(baseline_count, 3) if baseline_match is not False else 0.0
    robust_a_rms = _z_to_100(_positive_z(a_rms_ac, baseline_stats.get("a_rms_ac")))
    robust_a_p2p = _z_to_100(_positive_z(a_p2p, baseline_stats.get("a_p2p")))
    robust_g_std = _z_to_100(_positive_z(g_std, baseline_stats.get("g_std")))
    shared_components = [
        baseline_weight * robust_a_rms + (1.0 - baseline_weight) * fallback_a_rms,
        baseline_weight * robust_a_p2p + (1.0 - baseline_weight) * fallback_a_p2p,
        baseline_weight * robust_g_std + (1.0 - baseline_weight) * fallback_g_std,
    ]
    shared_score = sum(shared_components) / len(shared_components)
    shared_hits = _count_hits(shared_components, gate_cfg["feature_hit_min"])
    shared_strong_hits = _count_hits(shared_components, gate_cfg["feature_strong_min"])
    run_state_score = (
        0.45 * ratio_to_100(a_rms_ac / max(a_mean, EPS), 0.0015, 0.020)
        + 0.35 * ratio_to_100(g_std / max(g_mean, EPS), 0.015, 0.320)
        + 0.20 * ratio_to_100(max(lat_peak_ratio, z_peak_ratio), 0.10, 0.50)
    )
    gate_mode = "running"
    score = 0.0
    if not sampling_ok:
        gate_mode = "off_target_40hz"
        status = "normal"
        score = 0.0
    elif run_state_score < gate_cfg["run_watch_min"]:
        gate_mode = "non_running_suppressed"
        status = "normal"
        score = 0.0
    elif shared_hits >= 3 and shared_strong_hits >= 2 and run_state_score >= gate_cfg["run_candidate_min"]:
        status = "candidate_faults"
        score = min(100.0, 72.0 + 4.0 * max(0, shared_strong_hits - 2))
    elif shared_hits >= 2 and run_state_score >= gate_cfg["run_watch_min"]:
        status = "watch_only"
        score = min(59.0, 52.0 + 3.0 * max(0, shared_hits - 2) + 2.0 * max(0, shared_strong_hits - 1))
    else:
        status = "normal"
        score = max(0.0, min(44.0, 18.0 + 8.0 * shared_hits + 4.0 * shared_strong_hits))
        if run_state_score < gate_cfg["run_weak_min"]:
            gate_mode = "weak_running_suppressed"
    return {
        "status": status,
        "score": round(score, 2),
        "shared_abnormal_score": round(float(shared_score), 2),
        "baseline_mode": "mapping_mismatch_fallback"
        if baseline_match is False and baseline is not None
        else ("robust_baseline" if baseline_weight > 0.0 else "self_normalized_fallback"),
        "baseline_weight": round(float(baseline_weight), 3),
        "baseline_features": baseline_count,
        "baseline_match": baseline_match,
        "run_state_score": round(float(run_state_score), 2),
        "gate_mode": gate_mode,
        "sampling_ok_40hz": sampling_ok,
        "sampling_condition": sampling_condition,
    }


def _family_for_fault_type(fault_type: str) -> str:
    key = str(fault_type or "").strip().lower()
    if key in {"rope_looseness", "rope_tension_abnormal"}:
        return "rope"
    if key == "rubber_hardening":
        return "rubber"
    return key or "unknown"


def _specialized_score(result: dict[str, Any]) -> float:
    family = _family_for_fault_type(str(result.get("fault_type", "")))
    if family == "rope":
        return _safe_float(result.get("rope_specific_score"), _score(result))
    if family == "rubber":
        return _safe_float(result.get("rubber_specific_score"), _score(result))
    return _score(result)


def _type_watch_ready(result: dict[str, Any]) -> bool:
    raw = result.get("type_watch_ready")
    if raw is not None:
        return bool(raw)
    return str(result.get("level", "")).strip().lower() in {"watch", "warning", "alarm"}


def _decision_score(result: dict[str, Any], system_abnormality: dict[str, Any]) -> float:
    specific = _specialized_score(result)
    system_score = _safe_float(system_abnormality.get("score"), 0.0)
    return round(0.72 * specific + 0.28 * system_score, 2)


def _same_issue(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        str(left.get("fault_type", "")) == str(right.get("fault_type", ""))
        and abs(_score(left) - _score(right)) < 1e-6
        and str(left.get("level", "")) == str(right.get("level", ""))
    )


def _rope_only(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in items if _family_for_fault_type(item.get("fault_type", "")) == "rope"]


def _unknown_watch_issue(system_abnormality: dict[str, Any]) -> dict[str, Any]:
    score = max(_safe_float(system_abnormality.get("score"), 0.0), WATCH_SCORE)
    return {
        "fault_type": "unknown",
        "score": round(score, 2),
        "level": _level_from_score(score),
        "triggered": False,
        "quality_factor": 1.0,
        "reasons": [
            "mode=shared_abnormality_gate",
            f"shared_score={_safe_float(system_abnormality.get('shared_abnormal_score'), 0.0):.2f}",
            f"gate={system_abnormality.get('gate_mode', 'running')}",
        ],
        "feature_snapshot": {},
    }


def _screen_detectors(
    results: list[dict[str, Any]],
    *,
    system_abnormality: dict[str, Any],
    quality_ok: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    gate_cfg = SYSTEM_GATE_CONFIG
    candidates: list[dict[str, Any]] = []
    watch_faults: list[dict[str, Any]] = []
    for result in results:
        if _family_for_fault_type(result.get("fault_type", "")) != "rope":
            continue
        score = _score(result)
        quality = _quality(result)
        specialized_ready = bool(result.get("specialized_ready", bool(result.get("triggered", False))))
        type_watch_ready = _type_watch_ready(result)
        candidate_ready = (
            quality_ok
            and system_abnormality.get("status") == "candidate_faults"
            and specialized_ready
            and bool(result.get("triggered", False))
            and score >= gate_cfg["candidate_score"]
            and quality >= gate_cfg["candidate_quality"]
        )
        watch_ready = (
            quality_ok
            and system_abnormality.get("status") in {"candidate_faults", "watch_only"}
            and type_watch_ready
            and score >= gate_cfg["watch_score"]
            and quality >= gate_cfg["watch_quality"]
        )
        if candidate_ready:
            payload = _copy_result(result, screening="high_confidence")
            payload["decision_score"] = _decision_score(result, system_abnormality)
            candidates.append(payload)
        elif watch_ready:
            payload = _copy_result(result, screening="watch")
            payload["decision_score"] = _decision_score(result, system_abnormality)
            watch_faults.append(payload)
    candidates.sort(key=lambda item: (_safe_float(item.get("decision_score"), 0.0), _score(item)), reverse=True)
    watch_faults.sort(key=lambda item: (_safe_float(item.get("decision_score"), 0.0), _score(item)), reverse=True)
    return candidates, watch_faults


def run_all_rows(
    rows: list[dict[str, str]],
    source: str = "",
    *,
    baseline: dict | None = None,
    baseline_summary: dict[str, Any] | None = None,
    axis_mapping: dict[str, Any] | None = None,
) -> dict:
    features = build_feature_pack(rows, axis_mapping=axis_mapping)
    detector_features = dict(features)
    if baseline is not None:
        detector_features["baseline"] = baseline

    detectors = DETECTORS if DETECTORS else [PRIMARY_DETECTOR]
    detector_results = [detector(detector_features) for detector in detectors]
    detector_results = sorted(detector_results, key=_score, reverse=True)
    rope_primary = next((item for item in detector_results if _family_for_fault_type(item.get("fault_type", "")) == "rope"), {})
    rubber_primary = next((item for item in detector_results if _family_for_fault_type(item.get("fault_type", "")) == "rubber"), {})
    system_abnormality = _system_abnormality(detector_features, baseline)

    n_effective = int(features.get("n", 0))
    quality_ok = bool(features.get("sampling_ok_40hz", False))
    candidate_faults, watch_faults = _screen_detectors(
        detector_results,
        system_abnormality=system_abnormality,
        quality_ok=quality_ok,
    )
    top_candidate = candidate_faults[0] if candidate_faults else {}
    baseline_match = baseline_mapping_match(features, baseline)

    baseline_payload = dict(baseline_summary or {"mode": "disabled", "count": 0, "stats": 0})
    if baseline is not None:
        baseline_payload["mapping_match"] = baseline_match
        baseline_payload["axis_mapping_signature"] = str(baseline.get("axis_mapping_signature", ""))
        baseline_payload["axis_mapping_mode"] = str(baseline.get("axis_mapping_mode", "default"))
    else:
        baseline_payload["mapping_match"] = None

    if not quality_ok:
        screening_status = "low_quality"
        top_fault = detector_results[0] if detector_results else {}
        watch_faults = []
        candidate_faults = []
    elif candidate_faults:
        screening_status = "candidate_faults"
        top_fault = top_candidate
    elif watch_faults:
        screening_status = "watch_only"
        top_fault = watch_faults[0]
    elif system_abnormality.get("status") in {"candidate_faults", "watch_only"} or candidate_faults or watch_faults:
        screening_status = "watch_only"
        top_fault = _copy_result(_unknown_watch_issue(system_abnormality), screening="watch")
        top_fault["decision_score"] = _safe_float(system_abnormality.get("score"), 0.0)
        watch_faults = [top_fault]
        candidate_faults = []
    else:
        screening_status = "normal"
        top_fault = detector_results[0] if detector_results else {}

    auxiliary_results = [dict(item) for item in detector_results if not _same_issue(item, top_fault)] if top_fault else detector_results

    return {
        "input": str(source),
        "summary": {
            "n_raw": int(features.get("n_raw", 0)),
            "n_effective": int(features.get("n", 0)),
            "fs_hz": round(float(features.get("fs_hz", 0.0)), 4),
            "used_new_only": bool(features.get("used_new_only", False)),
            "new_ratio": round(float(features.get("new_ratio", 0.0)), 4),
            "sampling_ok_40hz": bool(features.get("sampling_ok_40hz", False)),
            "sampling_condition": str(features.get("sampling_condition", "unknown")),
            "axis_mapping_mode": str(features.get("axis_mapping_mode", "default")),
            "axis_mapping_signature": str(features.get("axis_mapping_signature", "")),
        },
        "baseline": baseline_payload,
        "screening": {
            "status": screening_status,
            "quality_ok": quality_ok,
            "high_confidence_min_score": SYSTEM_GATE_CONFIG["candidate_score"],
            "watch_min_score": SYSTEM_GATE_CONFIG["watch_score"],
            "candidate_count": len(candidate_faults),
            "watch_count": len(watch_faults),
            "sampling_condition": str(features.get("sampling_condition", "unknown")),
        },
        "system_abnormality": system_abnormality,
        "rope_primary": {
            "fault_type": str(rope_primary.get("fault_type", "")),
            "score": _score(rope_primary),
            "level": str(rope_primary.get("level", "normal")),
            "triggered": bool(rope_primary.get("triggered", False)),
            "rope_rule_score": float(rope_primary.get("rope_rule_score", 0.0)),
            "rope_branch": str(rope_primary.get("rope_branch", "")),
            "rope_spectral_snapshot": dict(rope_primary.get("rope_spectral_snapshot", {}))
            if isinstance(rope_primary.get("rope_spectral_snapshot"), dict)
            else {},
            "sampling_condition": str(rope_primary.get("sampling_condition", features.get("sampling_condition", "unknown"))),
            "axis_mapping_signature": str(rope_primary.get("axis_mapping_signature", features.get("axis_mapping_signature", ""))),
            "baseline_match": rope_primary.get("baseline_match"),
        },
        "rubber_primary": dict(rubber_primary) if rubber_primary else {},
        "top_fault": top_fault,
        "top_candidate": top_candidate,
        "candidate_faults": candidate_faults,
        "watch_faults": watch_faults,
        "primary_issue": dict(top_fault) if screening_status in {"candidate_faults", "watch_only"} else {},
        "auxiliary_results": auxiliary_results,
        "results": [top_fault, *auxiliary_results] if top_fault else auxiliary_results,
    }


def run_all(
    path: Path,
    *,
    baseline_json: Path | None = None,
    baseline_dir: Path | None = None,
    baseline_start_hhmm: str = "0000",
    baseline_end_hhmm: str = "2359",
) -> dict:
    baseline_payload: dict | None = None
    baseline_summary: dict[str, Any] = {"mode": "disabled", "count": 0, "stats": 0}

    if baseline_json is not None:
        baseline_payload = _load_baseline_json(baseline_json)
        if baseline_payload is not None:
            stats = baseline_payload.get("stats", baseline_payload)
            baseline_summary = {
                "mode": "json",
                "count": int(baseline_payload.get("count", 0) or 0),
                "stats": len(stats) if isinstance(stats, dict) else 0,
                "path": str(baseline_json),
            }
    elif baseline_dir is not None:
        baseline_payload = _build_baseline_from_dir(baseline_dir, baseline_start_hhmm, baseline_end_hhmm)
        if baseline_payload is not None:
            stats = baseline_payload.get("stats", {})
            baseline_summary = {
                "mode": "dir",
                "count": int(baseline_payload.get("count", 0) or 0),
                "stats": len(stats) if isinstance(stats, dict) else 0,
                "path": str(baseline_dir),
                "window": {"start_hhmm": baseline_start_hhmm, "end_hhmm": baseline_end_hhmm},
            }

    rows = load_rows(path)
    return run_all_rows(rows, source=str(path), baseline=baseline_payload, baseline_summary=baseline_summary)


def main() -> int:
    parser = argparse.ArgumentParser(description="电梯振动多故障候选筛查（规则版）")
    parser.add_argument("--input", required=True, help="输入CSV")
    parser.add_argument("--baseline-json", default="", help="可选：健康基线 JSON")
    parser.add_argument("--baseline-dir", default="", help="可选：健康样本目录")
    parser.add_argument("--baseline-start-hhmm", default="0000", help="基线开始时间 HHMM")
    parser.add_argument("--baseline-end-hhmm", default="2359", help="基线结束时间 HHMM")
    parser.add_argument("--pretty", action="store_true", help="格式化JSON输出")
    args = parser.parse_args()

    payload = run_all(
        Path(args.input),
        baseline_json=Path(args.baseline_json) if args.baseline_json else None,
        baseline_dir=Path(args.baseline_dir) if args.baseline_dir else None,
        baseline_start_hhmm=str(args.baseline_start_hhmm),
        baseline_end_hhmm=str(args.baseline_end_hhmm),
    )
    if args.pretty:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
