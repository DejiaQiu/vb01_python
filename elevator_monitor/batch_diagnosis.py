from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any

from .maintenance_workflow import build_maintenance_package
from .reporting_service import build_report_context, render_report_markdown
from .waveform_service import build_waveform_payload, load_waveform_rows
from report.fault_algorithms._base import build_clean_feature_baseline, build_feature_pack, load_rows
from report.fault_algorithms.run_all import BASELINE_KEYS, MIN_EFFECTIVE_SAMPLES, run_all_rows


_FILE_TS_PATTERN = re.compile(r"(\d{8})_(\d{6})")
_ELEVATOR_ID_PATTERN = re.compile(r"elevator[_-]?([A-Za-z0-9]+)", re.IGNORECASE)
_RISK_LEVELS = (
    (0.85, "critical"),
    (0.65, "high"),
    (0.40, "watch"),
    (0.0, "normal"),
)
_BASELINE_KEYS = tuple(dict.fromkeys(BASELINE_KEYS))


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


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _infer_elevator_id(*candidates: Any) -> str:
    for value in candidates:
        text = str(value or "").strip()
        if not text:
            continue
        match = _ELEVATOR_ID_PATTERN.search(text)
        if match:
            return f"elevator-{match.group(1)}"

    for value in candidates:
        text = str(value or "").strip()
        if not text:
            continue
        parts = [part for part in Path(text).parts if part]
        for part in reversed(parts):
            if re.fullmatch(r"\d{2,4}", part):
                return f"elevator-{part}"

    return "elevator-unknown"


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
        files = sorted(root.glob("*.csv"), key=_timestamp_key)
        timed_files = [path for path in files if _FILE_TS_PATTERN.search(path.name)]
        files = [path for path in timed_files if _in_range(path, baseline_start_hhmm, baseline_end_hhmm)] if timed_files else files
        feature_rows = [build_feature_pack(load_rows(path)) for path in files]
        if feature_rows:
            payload = build_clean_feature_baseline(feature_rows, _BASELINE_KEYS, min_samples=MIN_EFFECTIVE_SAMPLES)
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
    compacted = {
        "fault_type": str(payload.get("fault_type", "unknown")),
        "score": round(_safe_float(payload.get("score"), 0.0), 2),
        "level": str(payload.get("level", "normal")),
        "triggered": bool(payload.get("triggered", False)),
        "quality_factor": round(_safe_float(payload.get("quality_factor"), 0.0), 3),
        "screening": str(payload.get("screening", "")),
    }
    for key in (
        "sampling_condition",
        "axis_mapping_signature",
        "baseline_mode",
        "baseline_match",
        "type_watch_ready",
        "type_candidate_ready",
        "attribution_margin",
        "detector_family",
        "feature_hits",
        "feature_strong_hits",
    ):
        if key in payload:
            compacted[key] = payload.get(key)
    return compacted


def _preferred_issue(result: dict[str, Any]) -> dict[str, Any]:
    primary_issue = result.get("primary_issue", {})
    if isinstance(primary_issue, dict) and primary_issue:
        return primary_issue
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
        "rope_primary": _compact_fault(result.get("rope_primary", {})),
        "rubber_primary": _compact_fault(result.get("rubber_primary", {})),
        "candidate_faults": [_compact_fault(item) for item in candidate_faults if isinstance(item, dict)],
        "watch_faults": [_compact_fault(item) for item in watch_faults if isinstance(item, dict)],
    }


def _screening_rank(status: str) -> int:
    key = str(status or "").strip().lower()
    if key == "candidate_faults":
        return 2
    if key == "watch_only":
        return 1
    return 0


def _history_summary_issue(history: list[dict[str, Any]]) -> dict[str, Any]:
    abnormal_rows = [row for row in history if _screening_rank(str(row.get("screening_status", ""))) > 0]
    if not abnormal_rows:
        return {}
    # 批量汇总时优先看“重复出现的同类型归因”，而不是只看单窗最高分。
    typed_votes: dict[str, list[dict[str, Any]]] = {}
    for row in abnormal_rows:
        preferred = row.get("preferred_issue", {}) if isinstance(row.get("preferred_issue"), dict) else {}
        preferred_type = str(preferred.get("fault_type", "unknown")).strip() or "unknown"
        if preferred and preferred_type not in {"unknown", "normal"}:
            typed_votes.setdefault(preferred_type, []).append(dict(preferred))
            continue
        rope_primary = row.get("rope_primary", {}) if isinstance(row.get("rope_primary"), dict) else {}
        rubber_primary = row.get("rubber_primary", {}) if isinstance(row.get("rubber_primary"), dict) else {}
        rope_ready = bool(rope_primary.get("type_candidate_ready") or rope_primary.get("type_watch_ready"))
        rubber_ready = bool(rubber_primary.get("type_candidate_ready") or rubber_primary.get("type_watch_ready"))
        if rope_ready and not rubber_ready:
            typed_votes.setdefault("rope_looseness", []).append(dict(rope_primary))
        elif rubber_ready and not rope_ready:
            typed_votes.setdefault("rubber_hardening", []).append(dict(rubber_primary))
    if typed_votes:
        typed_counts = sorted(
            ((fault_type, len(items), max(_safe_float(item.get("score"), 0.0) for item in items)) for fault_type, items in typed_votes.items()),
            key=lambda item: (item[1], item[2]),
            reverse=True,
        )
        top_type, top_count, _ = typed_counts[0]
        second_count = typed_counts[1][1] if len(typed_counts) > 1 else 0
        if top_count >= 2 and top_count > second_count:
            strongest = max(typed_votes[top_type], key=lambda item: _safe_float(item.get("score"), 0.0))
            issue = {
                "fault_type": str(strongest.get("fault_type", top_type) or top_type),
                "score": round(max(45.0, min(59.0, _safe_float(strongest.get("score"), 0.0))), 2),
                "level": "watch",
                "triggered": False,
                "quality_factor": round(_safe_float(strongest.get("quality_factor"), 1.0), 3),
                "screening": "watch",
            }
            return issue
    strongest = max(
        abnormal_rows,
        key=lambda row: (
            _screening_rank(str(row.get("screening_status", ""))),
            _safe_float((row.get("preferred_issue") or {}).get("score"), 0.0),
        ),
    )
    issue = dict(strongest.get("preferred_issue", {})) if isinstance(strongest.get("preferred_issue"), dict) else {}
    if not issue:
        issue = dict(strongest.get("top_fault", {})) if isinstance(strongest.get("top_fault"), dict) else {}
    if not issue:
        return {}
    issue["screening"] = "watch"
    issue["triggered"] = False
    issue["level"] = "watch"
    return issue


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
        if fault_type in {"rope_looseness", "rope_tension_abnormal"}:
            return "建议尽快检查钢丝绳张力状态、张力均衡、曳引轮绳槽和钢丝绳外观。"
        if fault_type == "rubber_hardening":
            return "建议尽快检查曳引机减振橡胶是否老化、变硬或开裂。"
        return "建议尽快安排现场复核，确认候选故障是否成立。"
    if status == "watch_only":
        return "建议继续观察或尽快复测，不建议仅凭一次筛查结果直接拆检。"
    if status == "low_quality":
        return "建议重新采集一批更完整的数据后再判断。"
    return "当前更适合继续观察并维持常规维保计划。"


def _alert_level_from_status(status: str) -> str:
    if status == "candidate_faults":
        return "anomaly"
    if status == "watch_only":
        return "warning"
    return "normal"


def _build_report_outputs(
    *,
    latest_result: dict[str, Any],
    latest_issue: dict[str, Any],
    latest_status: str,
    risk: dict[str, Any],
    baseline_summary: dict[str, Any],
    baseline_payload: dict[str, Any] | None,
    latest_file: Path,
    input_dir: str,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    elevator_id = _infer_elevator_id(
        latest_result.get("input"),
        latest_file,
        baseline_summary.get("path", ""),
        input_dir,
    )
    issue_fault_type = str(latest_issue.get("fault_type", "unknown")).strip() or "unknown"
    issue_score = round(_safe_float(latest_issue.get("score"), 0.0), 2)
    alert_row = {
        "elevator_id": elevator_id,
        "ts_ms": str(int(time.time() * 1000)),
        "level": _alert_level_from_status(latest_status),
        "predictive_only": "0",
        "fault_type": issue_fault_type,
        "fault_confidence": f"{max(0.0, min(1.0, issue_score / 100.0)):.4f}",
        "risk_score": f"{_safe_float(risk.get('risk_score'), 0.0):.4f}",
        "risk_level_now": str(risk.get("risk_level_now", "normal")),
        "risk_24h": f"{_safe_float(risk.get('risk_24h'), 0.0):.4f}",
        "risk_level_24h": str(risk.get("risk_level_24h", "normal")),
        "alert_context_csv": str(latest_file),
    }
    health_payload = {
        "status": "scheduled_batch_diagnosis",
        "connected": True,
        "baseline_ready": str(baseline_summary.get("mode", "disabled")) != "disabled",
        "elevator_id": elevator_id,
        "last_fault_type": issue_fault_type,
        "last_fault_confidence": round(max(0.0, min(1.0, issue_score / 100.0)), 4),
        "last_risk_score": round(_safe_float(risk.get("risk_score"), 0.0), 4),
        "last_risk_level_now": str(risk.get("risk_level_now", "normal")),
        "last_risk_24h": round(_safe_float(risk.get("risk_24h"), 0.0), 4),
        "last_risk_level_24h": str(risk.get("risk_level_24h", "normal")),
    }
    maintenance_package = build_maintenance_package(
        alert_rows=[alert_row],
        health_payload=health_payload,
        site_name="",
        alert_csv_path="",
        health_json_path="",
        manifest_payload={},
        manifest_path="",
    )
    baseline_reference: dict[str, Any] = {"stats": {}}
    if isinstance(baseline_payload, dict):
        raw_stats = baseline_payload.get("stats", {})
        if isinstance(raw_stats, dict):
            for key in ("lateral_ratio", "lat_dom_freq_hz", "lat_low_band_ratio", "ag_corr"):
                item = raw_stats.get(key)
                if isinstance(item, dict):
                    baseline_reference["stats"][key] = {
                        "median": _safe_float(item.get("median"), 0.0),
                        "scale": _safe_float(item.get("scale"), 0.0),
                        "count": _safe_float(item.get("count"), 0.0),
                    }
        baseline_reference["count"] = _safe_int(baseline_payload.get("count"), 0)

    report_diagnosis = dict(latest_result)
    report_diagnosis["baseline_reference"] = baseline_reference
    try:
        waveform_rows, waveform_source = load_waveform_rows([], "", str(latest_file))
        waveform_payload = build_waveform_payload(
            waveform_rows,
            source=waveform_source,
            diagnosis_result=report_diagnosis,
        ) if waveform_rows else {}
    except Exception:
        waveform_payload = {}

    report_context = build_report_context(
        diagnosis_result=report_diagnosis,
        maintenance_package=maintenance_package,
        language="zh-CN",
        report_style="standard",
        waveform_payload=waveform_payload,
    )
    report_markdown_draft = render_report_markdown(report_context)
    report_summary = {
        "report_title": str(report_context.get("report_title", "")),
        "screening": dict(report_context.get("screening", {})) if isinstance(report_context.get("screening"), dict) else {},
        "preferred_issue": dict(report_context.get("preferred_issue", {})) if isinstance(report_context.get("preferred_issue"), dict) else {},
        "priority": str(report_context.get("priority", "")),
        "language": str(report_context.get("language", "zh-CN")),
    }
    return report_summary, report_markdown_draft, waveform_payload


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

    latest_status = str((latest_result.get("screening") or {}).get("status", "normal"))
    if latest_status == "normal":
        summary_issue = _history_summary_issue(history)
        if summary_issue:
            latest_result["screening"] = {
                **(latest_result.get("screening", {}) if isinstance(latest_result.get("screening"), dict) else {}),
                "status": "watch_only",
                "watch_count": 1,
                "candidate_count": 0,
            }
            latest_result["top_fault"] = dict(summary_issue)
            latest_result["primary_issue"] = dict(summary_issue)
            latest_result["top_candidate"] = {}
            latest_result["candidate_faults"] = []
            latest_result["watch_faults"] = [dict(summary_issue)]

    latest_issue = _preferred_issue(latest_result)
    latest_status = str((latest_result.get("screening") or {}).get("status", "normal"))
    risk = _build_risk(history)
    report_summary, report_markdown_draft, waveform_payload = _build_report_outputs(
        latest_result=latest_result,
        latest_issue=latest_issue,
        latest_status=latest_status,
        risk=risk,
        baseline_summary=baseline_summary,
        baseline_payload=baseline_payload,
        latest_file=latest_file,
        input_dir=input_dir,
    )

    payload = {
        "workflow_type": "scheduled_batch_diagnosis_v1",
        "generated_at_ms": int(time.time() * 1000),
        "input_dir": str(Path(input_dir).expanduser().resolve()) if str(input_dir).strip() else "",
        "files_scanned": len(files),
        "latest_file": str(latest_file),
        "latest_file_name": latest_file.name,
        "status": latest_status,
        "baseline": dict(latest_result.get("baseline", {})) if isinstance(latest_result.get("baseline"), dict) else baseline_summary,
        "primary_issue": _compact_fault(latest_result.get("primary_issue", {}) or latest_issue),
        "preferred_issue": _compact_fault(latest_issue),
        "rope_primary": _compact_fault(latest_result.get("rope_primary", {})),
        "rubber_primary": _compact_fault(latest_result.get("rubber_primary", {})),
        "system_abnormality": dict(latest_result.get("system_abnormality", {})) if isinstance(latest_result.get("system_abnormality"), dict) else {},
        "top_candidate": _compact_fault(latest_result.get("top_candidate", {})),
        "watch_faults": [_compact_fault(item) for item in latest_result.get("watch_faults", []) if isinstance(item, dict)],
        "auxiliary_results": [_compact_fault(item) for item in latest_result.get("auxiliary_results", []) if isinstance(item, dict)],
        "rope_timeline": {},
        "risk": risk,
        "recommendation": _status_recommendation(latest_status, str(latest_issue.get("fault_type", "unknown"))),
        "report_summary": report_summary,
        "report_markdown_draft": report_markdown_draft,
        "waveform_payload": waveform_payload,
        "latest_result": {
            "summary": dict(latest_result.get("summary", {})) if isinstance(latest_result.get("summary"), dict) else {},
            "baseline": dict(latest_result.get("baseline", {})) if isinstance(latest_result.get("baseline"), dict) else {},
            "screening": dict(latest_result.get("screening", {})) if isinstance(latest_result.get("screening"), dict) else {},
            "rope_primary": dict(latest_result.get("rope_primary", {})) if isinstance(latest_result.get("rope_primary"), dict) else {},
            "rubber_primary": dict(latest_result.get("rubber_primary", {})) if isinstance(latest_result.get("rubber_primary"), dict) else {},
            "system_abnormality": dict(latest_result.get("system_abnormality", {})) if isinstance(latest_result.get("system_abnormality"), dict) else {},
            "top_fault": _compact_fault(latest_result.get("top_fault", {})),
            "top_candidate": _compact_fault(latest_result.get("top_candidate", {})),
            "candidate_faults": [_compact_fault(item) for item in latest_result.get("candidate_faults", []) if isinstance(item, dict)],
            "watch_faults": [_compact_fault(item) for item in latest_result.get("watch_faults", []) if isinstance(item, dict)],
            "auxiliary_results": [_compact_fault(item) for item in latest_result.get("auxiliary_results", []) if isinstance(item, dict)],
            "rope_timeline": {},
            "waveform_payload": waveform_payload,
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
