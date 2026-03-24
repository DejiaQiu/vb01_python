from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

try:
    from ._base import SAMPLING_QUALITY_CONFIG, baseline_mapping_match, build_clean_feature_baseline, build_feature_pack, load_rows, parse_float, ratio_to_100
except ImportError:  # pragma: no cover
    from _base import SAMPLING_QUALITY_CONFIG, baseline_mapping_match, build_clean_feature_baseline, build_feature_pack, load_rows, parse_float, ratio_to_100


_PATTERN = re.compile(r"vibration_30s_\d{8}_(\d{6})\.csv$")

HIGH_CONFIDENCE_SCORE = 60.0
WATCH_SCORE = 45.0
MIN_EFFECTIVE_SAMPLES = int(SAMPLING_QUALITY_CONFIG["min_samples"])
EPS = 1e-9

# 通用异常门只回答“相对健康基线是否偏离”，不直接给故障类型。
# 这里混合少量整体振动特征和方向/频域特征，目的是在 rope/rubber 主判之前，
# 先筛出“确实不像健康状态”的窗口。
SYSTEM_BASELINE_FEATURES: tuple[dict[str, float | str], ...] = (
    {"key": "a_rms_ac", "weight": 1.0, "softness": 2.8, "floor_abs": 0.003, "floor_ratio": 0.15},
    {"key": "a_p2p", "weight": 1.0, "softness": 2.8, "floor_abs": 0.05, "floor_ratio": 0.18},
    {"key": "g_std", "weight": 1.0, "softness": 2.5, "floor_abs": 0.02, "floor_ratio": 0.12},
    {"key": "a_peak_std", "weight": 0.9, "softness": 2.5, "floor_abs": 0.005, "floor_ratio": 0.20},
    {"key": "a_pca_primary_ratio", "weight": 0.8, "softness": 2.5, "floor_abs": 0.05, "floor_ratio": 0.12},
    {"key": "a_band_log_ratio_0_5_over_5_20", "weight": 0.8, "softness": 2.5, "floor_abs": 0.04, "floor_ratio": 0.18},
    {"key": "lateral_ratio", "weight": 1.1, "softness": 2.2, "floor_abs": 0.10, "floor_ratio": 0.12},
    {"key": "lat_dom_freq_hz", "weight": 1.0, "softness": 2.0, "floor_abs": 0.35, "floor_ratio": 0.18},
    {"key": "lat_low_band_ratio", "weight": 1.1, "softness": 2.0, "floor_abs": 0.04, "floor_ratio": 0.15},
    {"key": "z_peak_ratio", "weight": 0.9, "softness": 2.0, "floor_abs": 0.02, "floor_ratio": 0.20},
)
BASELINE_KEYS = tuple(str(item["key"]) for item in SYSTEM_BASELINE_FEATURES)
SYSTEM_GATE_CONFIG = {
    "watch_score": WATCH_SCORE,
    "candidate_score": HIGH_CONFIDENCE_SCORE,
    "min_effective_samples": MIN_EFFECTIVE_SAMPLES,
    "feature_hit_min": 45.0,
    "feature_strong_min": 60.0,
    "shared_watch_min": 35.0,
    "shared_candidate_min": 48.0,
    "watch_hit_min": 3,
    "candidate_hit_min": 5,
    "candidate_strong_min": 2,
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
    files = sorted(input_dir.glob("*.csv"))
    timed_files = [path for path in files if _PATTERN.match(path.name)]
    if timed_files:
        return sorted([path for path in timed_files if _in_range(_hhmmss(path), start_hhmm, end_hhmm)], key=lambda p: _hhmmss(p))
    # 兼容离线切窗后的 segment_*.csv 基线目录；这类文件没有时刻信息，直接全量纳入。
    return files


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


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


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


def _deviation_z(
    value: float,
    stat: tuple[float, float] | None,
    *,
    floor_abs: float = 0.0,
    floor_ratio: float = 0.0,
) -> tuple[float, float]:
    if stat is None:
        return 0.0, max(1e-6, float(floor_abs))
    median, scale = stat
    effective_scale = max(float(scale), float(floor_abs), abs(float(median)) * float(floor_ratio), 1e-6)
    return abs(float(value) - float(median)) / effective_scale, effective_scale


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


def _system_baseline_components(
    features: dict[str, Any],
    baseline_stats: dict[str, tuple[float, float]],
) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    for spec in SYSTEM_BASELINE_FEATURES:
        key = str(spec["key"])
        stat = baseline_stats.get(key)
        if stat is None:
            continue
        value = _to_float(features.get(key))
        deviation_z, effective_scale = _deviation_z(
            value,
            stat,
            floor_abs=_safe_float(spec.get("floor_abs"), 0.0),
            floor_ratio=_safe_float(spec.get("floor_ratio"), 0.0),
        )
        score = _safe_float(spec.get("weight"), 1.0) * _z_to_100(deviation_z, _safe_float(spec.get("softness"), 2.0))
        components.append(
            {
                "key": key,
                "value": round(float(value), 6),
                "median": round(float(stat[0]), 6),
                "scale": round(float(stat[1]), 6),
                "effective_scale": round(float(effective_scale), 6),
                "z": round(float(deviation_z), 3),
                "score": round(float(score), 2),
            }
        )
    return components


def _system_abnormality(features: dict[str, Any], baseline: dict[str, Any] | None) -> dict[str, Any]:
    gate_cfg = SYSTEM_GATE_CONFIG
    sampling_ok = bool(features.get("sampling_ok", features.get("sampling_ok_40hz", False)))
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
    baseline_components = _system_baseline_components(features, baseline_stats) if baseline_stats else []
    baseline_count = len(baseline_components)
    baseline_weight = _normalize_weight(baseline_count, len(SYSTEM_BASELINE_FEATURES)) if baseline_match is not False else 0.0
    fallback_components = [
        {"key": "a_rms_ac", "score": round(float(fallback_a_rms), 2)},
        {"key": "a_p2p", "score": round(float(fallback_a_p2p), 2)},
        {"key": "g_std", "score": round(float(fallback_g_std), 2)},
    ]
    # 有健康基线时优先看“相对基线偏离”；没有基线再退回自归一化兜底。
    shared_component_payload = baseline_components if baseline_components else fallback_components
    shared_components = [float(item.get("score", 0.0)) for item in shared_component_payload]
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
        gate_mode = "sampling_low_quality"
        status = "normal"
        score = 0.0
    elif run_state_score < gate_cfg["run_watch_min"]:
        gate_mode = "non_running_suppressed"
        status = "normal"
        score = 0.0
    elif (
        shared_score >= gate_cfg["shared_candidate_min"]
        and shared_hits >= gate_cfg["candidate_hit_min"]
        and shared_strong_hits >= gate_cfg["candidate_strong_min"]
        and run_state_score >= gate_cfg["run_candidate_min"]
    ):
        status = "candidate_faults"
        score = min(100.0, shared_score)
    elif shared_score >= gate_cfg["shared_watch_min"] and shared_hits >= gate_cfg["watch_hit_min"] and run_state_score >= gate_cfg["run_watch_min"]:
        status = "watch_only"
        score = min(gate_cfg["candidate_score"] - 1.0, max(gate_cfg["watch_score"], shared_score))
    else:
        status = "normal"
        score = max(0.0, min(gate_cfg["watch_score"] - 1.0, shared_score))
        if run_state_score < gate_cfg["run_weak_min"]:
            gate_mode = "weak_running_suppressed"
    return {
        "status": status,
        "score": round(score, 2),
        "shared_abnormal_score": round(float(shared_score), 2),
        "baseline_mode": "mapping_mismatch_fallback"
        if baseline_match is False and baseline is not None
        else ("robust_baseline" if baseline_components else "self_normalized_fallback"),
        "baseline_weight": round(float(baseline_weight), 3),
        "baseline_features": baseline_count,
        "baseline_match": baseline_match,
        "run_state_score": round(float(run_state_score), 2),
        "gate_mode": gate_mode,
        "shared_hits": int(shared_hits),
        "shared_strong_hits": int(shared_strong_hits),
        "shared_feature_total": int(len(shared_components)),
        "top_deviations": sorted(shared_component_payload, key=lambda item: _safe_float(item.get("score"), 0.0), reverse=True)[:4],
        "sampling_ok": sampling_ok,
        "sampling_ok_40hz": sampling_ok,
        "sampling_condition": sampling_condition,
    }


def _generic_abnormal_issue(system_abnormality: dict[str, Any], *, screening: str) -> dict[str, Any]:
    score = _safe_float(system_abnormality.get("score"), 0.0)
    if screening == "high_confidence":
        score = max(score, HIGH_CONFIDENCE_SCORE)
    else:
        score = max(score, WATCH_SCORE)
    return {
        "fault_type": "unknown",
        "score": round(score, 2),
        "level": _level_from_score(score),
        "triggered": screening == "high_confidence",
        "quality_factor": 1.0,
        "reasons": [
            "mode=shared_abnormality_gate",
            f"shared_score={_safe_float(system_abnormality.get('shared_abnormal_score'), 0.0):.2f}",
            f"gate={system_abnormality.get('gate_mode', 'running')}",
        ],
        "feature_snapshot": {},
        "screening": screening,
    }


def run_all_rows(
    rows: list[dict[str, str]],
    source: str = "",
    *,
    baseline: dict | None = None,
    baseline_summary: dict[str, Any] | None = None,
    axis_mapping: dict[str, Any] | None = None,
) -> dict:
    features = build_feature_pack(rows, axis_mapping=axis_mapping)
    system_abnormality = _system_abnormality(features, baseline)

    quality_ok = bool(features.get("sampling_ok", features.get("sampling_ok_40hz", False)))
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
        top_fault = {}
        watch_faults = []
        candidate_faults = []
        top_candidate = {}
    elif system_abnormality.get("status") == "candidate_faults":
        screening_status = "candidate_faults"
        top_fault = _generic_abnormal_issue(system_abnormality, screening="high_confidence")
        top_candidate = dict(top_fault)
        candidate_faults = [dict(top_fault)]
        watch_faults = []
    elif system_abnormality.get("status") == "watch_only":
        screening_status = "watch_only"
        top_fault = _generic_abnormal_issue(system_abnormality, screening="watch")
        top_candidate = {}
        candidate_faults = []
        watch_faults = [dict(top_fault)]
    else:
        screening_status = "normal"
        top_fault = {}
        top_candidate = {}
        candidate_faults = []
        watch_faults = []

    return {
        "input": str(source),
        "summary": {
            "n_raw": int(features.get("n_raw", 0)),
            "n_effective": int(features.get("n", 0)),
            "fs_hz": round(float(features.get("fs_hz", 0.0)), 4),
            "used_new_only": bool(features.get("used_new_only", False)),
            "new_ratio": round(float(features.get("new_ratio", 0.0)), 4),
            "sampling_ok": bool(features.get("sampling_ok", features.get("sampling_ok_40hz", False))),
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
        "rope_primary": {},
        "rubber_primary": {},
        "top_fault": top_fault,
        "top_candidate": top_candidate,
        "candidate_faults": candidate_faults,
        "watch_faults": watch_faults,
        "primary_issue": dict(top_fault) if screening_status in {"candidate_faults", "watch_only"} else {},
        "auxiliary_results": [],
        "results": [top_fault] if top_fault else [],
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
