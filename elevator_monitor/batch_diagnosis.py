from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any

from report.fault_algorithms._base import build_feature_baseline, build_feature_pack, load_rows
from report.fault_algorithms.detect_rope_looseness import ROPE_BASELINE_KEYS
from report.fault_algorithms.detect_rubber_hardening import RUBBER_BASELINE_KEYS
from report.fault_algorithms.run_all import MIN_EFFECTIVE_SAMPLES, run_all_rows


_FILE_TS_PATTERN = re.compile(r"(\d{8})_(\d{6})")
_RISK_LEVELS = (
    (0.85, "critical"),
    (0.65, "high"),
    (0.40, "watch"),
    (0.0, "normal"),
)
_BASELINE_KEYS = tuple(dict.fromkeys(ROPE_BASELINE_KEYS + RUBBER_BASELINE_KEYS))


def _clamp01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _timestamp_key(path: Path) -> tuple[str, str, str]:
    match = _FILE_TS_PATTERN.search(path.name)
    if match:
        return (match.group(1), match.group(2), path.name)
    stat = path.stat()
    return (str(int(stat.st_mtime_ns)), "", path.name)


def _select_input_files(input_dir: str, csv_paths: list[str], max_files: int) -> list[Path]:
    if csv_paths:
        files = [Path(item).expanduser().resolve() for item in csv_paths if str(item).strip()]
        return [path for path in files if path.exists()]

    root = Path(input_dir).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"input dir not found: {root}")

    preferred = sorted(root.glob("vibration_30s_*.csv"), key=_timestamp_key)
    files = preferred if preferred else sorted(root.glob("*.csv"), key=_timestamp_key)
    if max_files > 0:
        files = files[-max_files:]
    return files


def _in_range(path: Path, start_hhmm: str, end_hhmm: str) -> bool:
    match = _FILE_TS_PATTERN.search(path.name)
    if not match:
        return True
    hhmm = match.group(2)[:4]
    return start_hhmm <= hhmm <= end_hhmm


def _build_baseline_summary(
    baseline_json: str,
    baseline_dir: str,
    baseline_start_hhmm: str,
    baseline_end_hhmm: str,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if str(baseline_json).strip():
        path = Path(baseline_json).expanduser().resolve()
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            stats = payload.get("stats", payload)
            return payload, {
                "mode": "json",
                "count": int(payload.get("count", 0) or 0),
                "stats": len(stats) if isinstance(stats, dict) else 0,
                "path": str(path),
            }

    if str(baseline_dir).strip():
        root = Path(baseline_dir).expanduser().resolve()
        files = sorted(
            [path for path in root.glob("vibration_30s_*.csv") if _in_range(path, baseline_start_hhmm, baseline_end_hhmm)],
            key=_timestamp_key,
        )
        feature_rows = [build_feature_pack(load_rows(path)) for path in files]
        if feature_rows:
            payload = build_feature_baseline(feature_rows, _BASELINE_KEYS, min_samples=MIN_EFFECTIVE_SAMPLES)
            payload["source"] = str(root)
            payload["window"] = {"start_hhmm": baseline_start_hhmm, "end_hhmm": baseline_end_hhmm}
            stats = payload.get("stats", {})
            return payload, {
                "mode": "dir",
                "count": int(payload.get("count", 0) or 0),
                "stats": len(stats) if isinstance(stats, dict) else 0,
                "path": str(root),
                "window": {"start_hhmm": baseline_start_hhmm, "end_hhmm": baseline_end_hhmm},
            }

    return None, {"mode": "disabled", "count": 0, "stats": 0}


def _compact_fault(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or not payload:
        return {}
    return {
        "fault_type": str(payload.get("fault_type", "unknown")),
        "score": round(_safe_float(payload.get("score"), 0.0), 2),
        "level": str(payload.get("level", "normal")),
        "triggered": bool(payload.get("triggered", False)),
        "quality_factor": round(_safe_float(payload.get("quality_factor"), 0.0), 3),
        "screening": str(payload.get("screening", "")),
    }


def _preferred_issue(result: dict[str, Any]) -> dict[str, Any]:
    screening = result.get("screening", {}) if isinstance(result.get("screening"), dict) else {}
    status = str(screening.get("status", "normal"))
    if status == "candidate_faults":
        candidate = result.get("top_candidate", {})
        if isinstance(candidate, dict) and candidate:
            return candidate
    if status == "watch_only":
        watch_faults = result.get("watch_faults", [])
        if isinstance(watch_faults, list) and watch_faults:
            first = watch_faults[0]
            if isinstance(first, dict):
                return first
    return {}


def _history_entry(path: Path, result: dict[str, Any]) -> dict[str, Any]:
    screening = result.get("screening", {}) if isinstance(result.get("screening"), dict) else {}
    candidate_faults = result.get("candidate_faults", []) if isinstance(result.get("candidate_faults"), list) else []
    watch_faults = result.get("watch_faults", []) if isinstance(result.get("watch_faults"), list) else []
    preferred = _preferred_issue(result)
    return {
        "path": str(path),
        "name": path.name,
        "screening_status": str(screening.get("status", "normal")),
        "summary": dict(result.get("summary", {})) if isinstance(result.get("summary"), dict) else {},
        "top_fault": _compact_fault(result.get("top_fault", {})),
        "top_candidate": _compact_fault(result.get("top_candidate", {})),
        "preferred_issue": _compact_fault(preferred),
        "candidate_faults": [_compact_fault(item) for item in candidate_faults if isinstance(item, dict)],
        "watch_faults": [_compact_fault(item) for item in watch_faults if isinstance(item, dict)],
    }


def _risk_level(score: float) -> str:
    for threshold, label in _RISK_LEVELS:
        if score >= threshold:
            return label
    return "normal"


def _build_risk(history: list[dict[str, Any]]) -> dict[str, Any]:
    if not history:
        return {
            "risk_score": 0.0,
            "risk_level_now": "normal",
            "risk_24h": 0.0,
            "risk_level_24h": "normal",
            "reasons": ["history=empty"],
        }

    latest = history[-1]
    latest_status = str(latest.get("screening_status", "normal"))
    latest_issue = latest.get("preferred_issue", {}) if isinstance(latest.get("preferred_issue"), dict) else {}
    latest_fault_type = str(latest_issue.get("fault_type", "unknown"))
    latest_score = _safe_float(latest_issue.get("score"), 0.0)
    current_component = latest_score / 100.0 if latest_status in {"candidate_faults", "watch_only"} else 0.0

    abnormal_history = [row for row in history if str(row.get("screening_status", "")) in {"candidate_faults", "watch_only"}]
    same_fault_history = [
        row
        for row in abnormal_history
        if str((row.get("preferred_issue") or {}).get("fault_type", "unknown")) == latest_fault_type
    ]
    persistence_component = len(same_fault_history) / max(1, len(history))
    quality_component = len([row for row in history if str(row.get("screening_status", "")) != "low_quality"]) / max(1, len(history))

    same_fault_scores = [
        _safe_float((row.get("preferred_issue") or {}).get("score"), 0.0)
        for row in history
        if str((row.get("preferred_issue") or {}).get("fault_type", "unknown")) == latest_fault_type
    ]
    if len(same_fault_scores) >= 2:
        trend_component = _clamp01((same_fault_scores[-1] - same_fault_scores[0]) / 40.0)
    else:
        trend_component = 0.0

    previous_abnormal = any(str(row.get("screening_status", "")) in {"candidate_faults", "watch_only"} for row in history[:-1])
    recovery_component = 1.0 if latest_status == "normal" and previous_abnormal else 0.0

    risk_score = _clamp01(
        0.45 * current_component
        + 0.25 * persistence_component
        + 0.20 * trend_component
        + 0.10 * quality_component
        - 0.15 * recovery_component
    )
    risk_24h = _clamp01(risk_score + 0.20 * trend_component + 0.15 * persistence_component)

    reasons = [
        f"current={current_component:.2f}",
        f"persistence={persistence_component:.2f}",
        f"trend={trend_component:.2f}",
        f"quality={quality_component:.2f}",
    ]
    if recovery_component > 0.0:
        reasons.append("recovery=1.00")
    if latest_fault_type and latest_fault_type != "unknown":
        reasons.append(f"focus_fault={latest_fault_type}")

    return {
        "risk_score": round(risk_score, 4),
        "risk_level_now": _risk_level(risk_score),
        "risk_24h": round(risk_24h, 4),
        "risk_level_24h": _risk_level(risk_24h),
        "reasons": reasons,
    }


def _status_recommendation(status: str, fault_type: str) -> str:
    if status == "candidate_faults":
        if fault_type == "rope_looseness":
            return "建议尽快检查钢丝绳张力均衡、曳引轮绳槽和钢丝绳外观。"
        if fault_type == "rubber_hardening":
            return "建议尽快检查曳引机减振橡胶是否老化、变硬或开裂。"
        return "建议尽快安排现场复核，确认候选故障是否成立。"
    if status == "watch_only":
        return "建议继续观察或尽快复测，不建议仅凭一次筛查结果直接拆检。"
    if status == "low_quality":
        return "建议重新采集一批更完整的数据后再判断。"
    return "当前更适合继续观察并维持常规维保计划。"


def _write_latest_json(path: str, payload: dict[str, Any]) -> str:
    latest_path = Path(path).expanduser().resolve()
    latest_path.parent.mkdir(parents=True, exist_ok=True)
    latest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(latest_path)


def _append_history_jsonl(path: str, payload: dict[str, Any]) -> str:
    history_path = Path(path).expanduser().resolve()
    history_path.parent.mkdir(parents=True, exist_ok=True)
    line = {
        "generated_at_ms": payload.get("generated_at_ms"),
        "status": payload.get("status"),
        "latest_file": payload.get("latest_file"),
        "preferred_issue": payload.get("preferred_issue"),
        "risk": payload.get("risk"),
    }
    with history_path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(line, ensure_ascii=False) + "\n")
    return str(history_path)


def load_latest_status(path: str) -> dict[str, Any]:
    latest_path = Path(path).expanduser().resolve()
    if not latest_path.exists():
        raise FileNotFoundError(f"latest status not found: {latest_path}")
    payload = json.loads(latest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("latest status payload must be a JSON object")
    return payload


def run_batch_diagnosis(
    *,
    input_dir: str = "",
    csv_paths: list[str] | None = None,
    max_files: int = 12,
    baseline_json: str = "",
    baseline_dir: str = "",
    baseline_start_hhmm: str = "0000",
    baseline_end_hhmm: str = "2359",
    latest_json: str = "",
    history_jsonl: str = "",
    write_outputs: bool = True,
) -> dict[str, Any]:
    files = _select_input_files(input_dir, csv_paths or [], max(1, int(max_files)))
    if not files:
        raise FileNotFoundError("no csv files selected for batch diagnosis")

    baseline_payload, baseline_summary = _build_baseline_summary(
        baseline_json=baseline_json,
        baseline_dir=baseline_dir,
        baseline_start_hhmm=baseline_start_hhmm,
        baseline_end_hhmm=baseline_end_hhmm,
    )

    history: list[dict[str, Any]] = []
    latest_result: dict[str, Any] = {}
    latest_file = files[-1]

    for path in files:
        rows = load_rows(path)
        result = run_all_rows(
            rows,
            source=str(path),
            baseline=baseline_payload,
            baseline_summary=baseline_summary,
        )
        history.append(_history_entry(path, result))
        if path == latest_file:
            latest_result = result

    latest_issue = _preferred_issue(latest_result)
    latest_status = str((latest_result.get("screening") or {}).get("status", "normal"))
    risk = _build_risk(history)

    payload = {
        "workflow_type": "scheduled_batch_diagnosis_v1",
        "generated_at_ms": int(time.time() * 1000),
        "input_dir": str(Path(input_dir).expanduser().resolve()) if str(input_dir).strip() else "",
        "files_scanned": len(files),
        "latest_file": str(latest_file),
        "latest_file_name": latest_file.name,
        "status": latest_status,
        "baseline": baseline_summary,
        "preferred_issue": _compact_fault(latest_issue),
        "top_candidate": _compact_fault(latest_result.get("top_candidate", {})),
        "watch_faults": [_compact_fault(item) for item in latest_result.get("watch_faults", []) if isinstance(item, dict)],
        "risk": risk,
        "recommendation": _status_recommendation(latest_status, str(latest_issue.get("fault_type", "unknown"))),
        "latest_result": {
            "summary": dict(latest_result.get("summary", {})) if isinstance(latest_result.get("summary"), dict) else {},
            "screening": dict(latest_result.get("screening", {})) if isinstance(latest_result.get("screening"), dict) else {},
            "top_fault": _compact_fault(latest_result.get("top_fault", {})),
            "top_candidate": _compact_fault(latest_result.get("top_candidate", {})),
            "candidate_faults": [_compact_fault(item) for item in latest_result.get("candidate_faults", []) if isinstance(item, dict)],
            "watch_faults": [_compact_fault(item) for item in latest_result.get("watch_faults", []) if isinstance(item, dict)],
        },
        "history": history,
    }

    outputs: dict[str, str] = {}
    if write_outputs and str(latest_json).strip():
        outputs["latest_json"] = _write_latest_json(latest_json, payload)
    if write_outputs and str(history_jsonl).strip():
        outputs["history_jsonl"] = _append_history_jsonl(history_jsonl, payload)
    if outputs:
        payload["output_files"] = outputs

    return payload


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="定时批诊断：对最近一批 CSV 运行候选故障筛查并输出最新状态。")
    parser.add_argument("--input-dir", default="", help="输入目录，默认从这里挑最近的 CSV")
    parser.add_argument("--csv-path", action="append", default=[], help="可选：显式指定 CSV，可重复传入")
    parser.add_argument("--max-files", type=int, default=12, help="从目录中最多挑最近多少个 CSV")
    parser.add_argument("--baseline-json", default="", help="可选：健康基线 JSON")
    parser.add_argument("--baseline-dir", default="", help="可选：健康样本目录")
    parser.add_argument("--baseline-start-hhmm", default="0000", help="基线开始时间 HHMM")
    parser.add_argument("--baseline-end-hhmm", default="2359", help="基线结束时间 HHMM")
    parser.add_argument("--latest-json", default="data/diagnosis/latest_status.json", help="最新状态输出路径")
    parser.add_argument("--history-jsonl", default="data/diagnosis/history.jsonl", help="历史状态输出路径")
    parser.add_argument("--no-write", action="store_true", help="只输出结果，不落盘")
    parser.add_argument("--pretty", action="store_true", help="格式化 JSON 输出")
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    payload = run_batch_diagnosis(
        input_dir=str(args.input_dir),
        csv_paths=list(args.csv_path or []),
        max_files=int(args.max_files),
        baseline_json=str(args.baseline_json),
        baseline_dir=str(args.baseline_dir),
        baseline_start_hhmm=str(args.baseline_start_hhmm),
        baseline_end_hhmm=str(args.baseline_end_hhmm),
        latest_json=str(args.latest_json),
        history_jsonl=str(args.history_jsonl),
        write_outputs=not bool(args.no_write),
    )
    if args.pretty:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
