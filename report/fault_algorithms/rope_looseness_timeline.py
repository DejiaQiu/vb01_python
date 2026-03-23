from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

try:
    from ._base import SAMPLING_QUALITY_CONFIG, build_clean_feature_baseline, build_feature_pack, load_rows
    from .detect_rope_looseness import ROPE_BASELINE_KEYS, ROPE_RULE_CONFIG, detect
except ImportError:  # pragma: no cover
    from _base import SAMPLING_QUALITY_CONFIG, build_clean_feature_baseline, build_feature_pack, load_rows
    from detect_rope_looseness import ROPE_BASELINE_KEYS, ROPE_RULE_CONFIG, detect


_PATTERN = re.compile(r"vibration_30s_\d{8}_(\d{6})\.csv$")
TIMELINE_MIN_SAMPLES = int(SAMPLING_QUALITY_CONFIG["min_samples"])


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
    feature_rows = []
    for path in _select_files(input_dir, start_hhmm, end_hhmm):
        rows = load_rows(path)
        feature_rows.append(build_feature_pack(rows))
    if not feature_rows:
        return None
    baseline = build_clean_feature_baseline(feature_rows, ROPE_BASELINE_KEYS, min_samples=TIMELINE_MIN_SAMPLES)
    baseline["source"] = str(input_dir)
    baseline["window"] = {"start_hhmm": start_hhmm, "end_hhmm": end_hhmm}
    return baseline


def _apply_consecutive_confirmation(rows: list[dict], confirm_windows: int) -> None:
    k = max(1, int(confirm_windows))
    if k <= 1:
        for row in rows:
            row["confirmed_triggered"] = bool(row["raw_triggered"]) and not bool(row.get("skip_confirmation"))
        return

    for row in rows:
        row["confirmed_triggered"] = False

    start = None
    for i, row in enumerate(rows):
        if row.get("skip_confirmation"):
            continue
        if row["raw_triggered"]:
            if start is None:
                start = i
        else:
            if start is not None:
                run_indices = [j for j in range(start, i) if not rows[j].get("skip_confirmation")]
                if len(run_indices) >= k:
                    for j in run_indices:
                        if rows[j]["raw_triggered"]:
                            rows[j]["confirmed_triggered"] = True
            start = None

    if start is not None:
        run_indices = [j for j in range(start, len(rows)) if not rows[j].get("skip_confirmation")]
        if len(run_indices) >= k:
            for j in run_indices:
                if rows[j]["raw_triggered"]:
                    rows[j]["confirmed_triggered"] = True


def run_timeline(
    *,
    input_dir: Path,
    start_hhmm: str,
    end_hhmm: str,
    min_score: float,
    confirm_windows: int,
    baseline_json: Path | None = None,
    baseline_dir: Path | None = None,
    baseline_start_hhmm: str = "0000",
    baseline_end_hhmm: str = "2359",
) -> dict:
    files = _select_files(input_dir, start_hhmm, end_hhmm)
    rows: list[dict] = []
    baseline_payload: dict | None = None
    baseline_summary = {"mode": "disabled", "count": 0, "stats": 0}

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

    for path in files:
        hhmmss = _hhmmss(path)
        features = build_feature_pack(load_rows(path))
        if baseline_payload is not None:
            features = dict(features)
            features["baseline"] = baseline_payload
        result = detect(features)
        score = float(result.get("score", 0.0))
        sampling_ok = bool(features.get("sampling_ok", features.get("sampling_ok_40hz", False)))
        raw_triggered = sampling_ok and score >= float(min_score)

        rows.append(
            {
                "hhmmss": hhmmss,
                "file": path.name,
                "score": round(score, 2),
                "level": result.get("level", "normal"),
                "mode": str(result.get("reasons", ["mode=unknown"])[0]).replace("mode=", ""),
                "raw_triggered": raw_triggered,
                "confirmed_triggered": False,
                "quality_factor": float(result.get("quality_factor", 0.0)),
                "n": int(result.get("feature_snapshot", {}).get("n", 0)),
                "sampling_condition": str(features.get("sampling_condition", "unknown")),
                "skip_confirmation": bool(
                    not sampling_ok
                    or result.get("quality_factor", 0.0) < 0.45
                    or result.get("feature_snapshot", {}).get("n", 0) < TIMELINE_MIN_SAMPLES
                ),
            }
        )

    _apply_consecutive_confirmation(rows, confirm_windows=confirm_windows)

    out = {
        "input_dir": str(input_dir),
        "window": {"start_hhmm": start_hhmm, "end_hhmm": end_hhmm},
        "baseline": baseline_summary,
        "rule": {"min_score": float(min_score), "confirm_windows": int(confirm_windows)},
        "count": len(rows),
        "raw_trigger_count": sum(1 for row in rows if row["raw_triggered"]),
        "confirmed_trigger_count": sum(1 for row in rows if row["confirmed_triggered"]),
        "rows": rows,
    }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="钢丝绳松动时间序列确认（连续命中才报警）")
    parser.add_argument("--input-dir", default="report", help="输入目录（包含 vibration_30s_*.csv）")
    parser.add_argument("--start-hhmm", default="0000", help="开始时间 HHMM")
    parser.add_argument("--end-hhmm", default="2359", help="结束时间 HHMM")
    parser.add_argument("--baseline-json", default="", help="可选：健康基线 JSON")
    parser.add_argument("--baseline-dir", default="", help="可选：健康样本目录")
    parser.add_argument("--baseline-start-hhmm", default="0000", help="基线开始时间 HHMM")
    parser.add_argument("--baseline-end-hhmm", default="2359", help="基线结束时间 HHMM")
    parser.add_argument("--min-score", type=float, default=float(ROPE_RULE_CONFIG["watch_score"]), help="单窗触发分数阈值")
    parser.add_argument("--confirm-windows", type=int, default=2, help="连续命中窗口数")
    parser.add_argument("--pretty", action="store_true", help="格式化输出JSON")
    args = parser.parse_args()

    payload = run_timeline(
        input_dir=Path(args.input_dir),
        start_hhmm=str(args.start_hhmm),
        end_hhmm=str(args.end_hhmm),
        min_score=float(args.min_score),
        confirm_windows=int(args.confirm_windows),
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
