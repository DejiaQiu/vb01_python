from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Callable

try:
    from ._base import build_feature_baseline, build_feature_pack, load_rows
    from .detect_rope_looseness import ROPE_BASELINE_KEYS, detect as detect_rope_looseness
    from .detect_rubber_hardening import RUBBER_BASELINE_KEYS, detect as detect_rubber_hardening
except ImportError:  # pragma: no cover
    from _base import build_feature_baseline, build_feature_pack, load_rows
    from detect_rope_looseness import ROPE_BASELINE_KEYS, detect as detect_rope_looseness
    from detect_rubber_hardening import RUBBER_BASELINE_KEYS, detect as detect_rubber_hardening


_PATTERN = re.compile(r"vibration_30s_\d{8}_(\d{6})\.csv$")

HIGH_CONFIDENCE_SCORE = 60.0
WATCH_SCORE = 45.0
HIGH_CONFIDENCE_QUALITY = 0.80
WATCH_QUALITY = 0.60
MIN_EFFECTIVE_SAMPLES = 8
BASELINE_KEYS = tuple(dict.fromkeys(ROPE_BASELINE_KEYS + RUBBER_BASELINE_KEYS))

DETECTORS: list[Callable[[dict[str, Any]], dict[str, Any]]] = [
    detect_rope_looseness,
    detect_rubber_hardening,
]


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
    baseline = build_feature_baseline(feature_rows, BASELINE_KEYS, min_samples=MIN_EFFECTIVE_SAMPLES)
    baseline["source"] = str(input_dir)
    baseline["window"] = {"start_hhmm": start_hhmm, "end_hhmm": end_hhmm}
    return baseline


def _quality(result: dict[str, Any]) -> float:
    return float(result.get("quality_factor", 0.0))


def _score(result: dict[str, Any]) -> float:
    return float(result.get("score", 0.0))


def _copy_result(result: dict[str, Any], *, screening: str) -> dict[str, Any]:
    payload = dict(result)
    payload["screening"] = screening
    return payload


def _screen_results(results: list[dict[str, Any]], features: dict[str, Any]) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    n_effective = int(features.get("n", 0))
    quality_ok = n_effective >= MIN_EFFECTIVE_SAMPLES

    candidates: list[dict[str, Any]] = []
    watch_faults: list[dict[str, Any]] = []

    for result in results:
        score = _score(result)
        quality = _quality(result)
        triggered = bool(result.get("triggered", False))
        if quality_ok and triggered and score >= HIGH_CONFIDENCE_SCORE and quality >= HIGH_CONFIDENCE_QUALITY:
            candidates.append(_copy_result(result, screening="high_confidence"))
            continue
        if quality_ok and score >= WATCH_SCORE and quality >= WATCH_QUALITY:
            watch_faults.append(_copy_result(result, screening="watch"))

    if not quality_ok:
        status = "low_quality"
    elif candidates:
        status = "candidate_faults"
    elif watch_faults:
        status = "watch_only"
    else:
        status = "normal"
    return status, candidates, watch_faults


def run_all_rows(
    rows: list[dict[str, str]],
    source: str = "",
    *,
    baseline: dict | None = None,
    baseline_summary: dict[str, Any] | None = None,
) -> dict:
    features = build_feature_pack(rows)
    detector_features = dict(features)
    if baseline is not None:
        detector_features["baseline"] = baseline

    results = [detector(detector_features) for detector in DETECTORS]
    results = sorted(results, key=_score, reverse=True)

    screening_status, candidate_faults, watch_faults = _screen_results(results, detector_features)
    top_fault = results[0] if results else {}
    top_candidate = candidate_faults[0] if candidate_faults else {}

    return {
        "input": str(source),
        "summary": {
            "n_raw": int(features.get("n_raw", 0)),
            "n_effective": int(features.get("n", 0)),
            "fs_hz": round(float(features.get("fs_hz", 0.0)), 4),
            "used_new_only": bool(features.get("used_new_only", False)),
            "new_ratio": round(float(features.get("new_ratio", 0.0)), 4),
        },
        "baseline": baseline_summary or {"mode": "disabled", "count": 0, "stats": 0},
        "screening": {
            "status": screening_status,
            "quality_ok": int(features.get("n", 0)) >= MIN_EFFECTIVE_SAMPLES,
            "high_confidence_min_score": HIGH_CONFIDENCE_SCORE,
            "watch_min_score": WATCH_SCORE,
            "candidate_count": len(candidate_faults),
            "watch_count": len(watch_faults),
        },
        "top_fault": top_fault,
        "top_candidate": top_candidate,
        "candidate_faults": candidate_faults,
        "watch_faults": watch_faults,
        "results": results,
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
