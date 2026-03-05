from __future__ import annotations

import argparse
import csv
import json
import time
from collections import Counter, deque
from pathlib import Path
from typing import Any, Optional


_TRUTHY = {"1", "true", "yes", "y", "on"}
_RISK_PRIORITY = {
    "critical": ("P1", "urgent_inspection", 1),
    "high": ("P2", "dispatch_4h", 4),
    "watch": ("P3", "dispatch_24h", 24),
    "normal": ("P4", "remote_watch", 72),
}
_FAULT_LIBRARY = {
    "door_stuck": {
        "actions": [
            "Inspect door motor current and opening resistance.",
            "Check door guide rail contamination and lubrication.",
            "Verify door lock and encoder alignment.",
        ],
        "parts": ["door roller", "door belt", "door lock switch"],
    },
    "bearing_wear": {
        "actions": [
            "Inspect traction motor bearing temperature and noise.",
            "Check lubrication status and shaft radial play.",
            "Confirm whether bearing resonance matches the recent trend.",
        ],
        "parts": ["motor bearing", "lubricant"],
    },
    "mechanical_looseness": {
        "actions": [
            "Inspect anchor bolts and frame fasteners.",
            "Recheck coupling tightness and vibration isolation mounts.",
            "Retest after torque recovery.",
        ],
        "parts": ["fastener kit", "vibration pad"],
    },
    "rope_looseness": {
        "actions": [
            "Check rope tension balance across all ropes.",
            "Inspect traction sheave groove wear and rope slip marks.",
            "Rebalance tension before returning to full load.",
        ],
        "parts": ["rope tension gauge", "rope clamp"],
    },
    "rail_wear": {
        "actions": [
            "Inspect guide rail and shoe wear marks.",
            "Verify alignment and lubrication state.",
            "Compare rail wear trend against previous inspection records.",
        ],
        "parts": ["guide shoe", "lubricant"],
    },
    "impact_shock": {
        "actions": [
            "Inspect brake engagement and release timing.",
            "Check motor base and coupling for intermittent impact.",
            "Review recent starts/stops around the alert window.",
        ],
        "parts": ["brake pad", "coupling insert"],
    },
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _is_truthy(value: Any) -> bool:
    return str(value).strip().lower() in _TRUTHY


def load_recent_alerts(path: str, limit: int = 50) -> list[dict[str, str]]:
    csv_path = Path(path)
    if not csv_path.exists():
        return []

    rows: deque[dict[str, str]] = deque(maxlen=max(1, int(limit)))
    with csv_path.open("r", encoding="utf-8", newline="") as fp:
        for row in csv.DictReader(fp):
            rows.append(dict(row))
    return list(rows)


def load_optional_json(path: str) -> dict[str, Any]:
    if not path:
        return {}
    json_path = Path(path)
    if not json_path.exists():
        return {}
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _fault_playbook(fault_type: str, predictive_only: bool) -> tuple[list[str], list[str]]:
    key = fault_type.strip().lower()
    if key in _FAULT_LIBRARY:
        payload = _FAULT_LIBRARY[key]
        return list(payload["actions"]), list(payload["parts"])

    actions = [
        "Inspect the mechanical path around the motor and traction system.",
        "Compare the alert context waveform against the site baseline.",
        "Confirm whether the anomaly repeats under both up and down runs.",
    ]
    if predictive_only:
        actions.insert(0, "Prioritize remote review before dispatching a full repair team.")
    return actions, []


def _select_priority(level: str, risk_level_24h: str, predictive_only: bool) -> tuple[str, str, int]:
    normalized_level = level.strip().lower()
    if normalized_level == "anomaly":
        return "P1", "urgent_inspection", 1
    if normalized_level == "warning" and not predictive_only:
        return "P2", "dispatch_4h", 4
    return _RISK_PRIORITY.get(risk_level_24h.strip().lower(), _RISK_PRIORITY["normal"])


def _model_context(manifest_payload: Optional[dict[str, Any]], manifest_path: str) -> dict[str, Any]:
    if not manifest_payload:
        return {"manifest_path": manifest_path, "model_ids": [], "model_names": []}

    models = manifest_payload.get("models", [])
    if not isinstance(models, list):
        models = []
    return {
        "manifest_path": manifest_path,
        "model_ids": [str(item.get("id", "")) for item in models if isinstance(item, dict) and item.get("id")],
        "model_names": [str(item.get("name", "")) for item in models if isinstance(item, dict) and item.get("name")],
    }


def build_maintenance_package(
    *,
    alert_rows: list[dict[str, str]],
    health_payload: Optional[dict[str, Any]] = None,
    site_name: str = "",
    alert_csv_path: str = "",
    health_json_path: str = "",
    manifest_payload: Optional[dict[str, Any]] = None,
    manifest_path: str = "",
) -> dict[str, Any]:
    health = health_payload or {}
    latest = dict(alert_rows[-1]) if alert_rows else {}

    elevator_id = str(latest.get("elevator_id") or health.get("elevator_id") or "elevator-unknown")
    level = str(latest.get("level", "normal"))
    predictive_only = _is_truthy(latest.get("predictive_only", "0"))
    fault_type = str(latest.get("fault_type", "")).strip() or str(health.get("last_fault_type", "unknown"))
    if not fault_type:
        fault_type = "unknown"

    risk_level_24h = str(latest.get("risk_level_24h") or health.get("last_risk_level_24h") or "normal")
    priority, maintenance_mode, dispatch_hours = _select_priority(level, risk_level_24h, predictive_only)
    actions, parts = _fault_playbook(fault_type, predictive_only)

    recent_faults = [
        str(row.get("fault_type", "")).strip()
        for row in alert_rows
        if str(row.get("fault_type", "")).strip() and str(row.get("fault_type", "")).strip() not in {"unknown", "normal"}
    ]
    fault_counter = Counter(recent_faults)
    top_faults = [{"fault_type": name, "count": count} for name, count in fault_counter.most_common(3)]

    risk_score = _safe_float(latest.get("risk_score"), _safe_float(health.get("last_risk_score"), 0.0))
    risk_24h = _safe_float(latest.get("risk_24h"), _safe_float(health.get("last_risk_24h"), 0.0))
    degradation_slope = _safe_float(latest.get("degradation_slope"), _safe_float(health.get("last_degradation_slope"), 0.0))
    fault_confidence = _safe_float(latest.get("fault_confidence"), _safe_float(health.get("last_fault_confidence"), 0.0))

    status = "action_required" if priority in {"P1", "P2", "P3"} else "observe"
    summary = (
        f"{elevator_id} requires {maintenance_mode} with priority {priority}. "
        f"Latest fault={fault_type}, risk_24h={risk_level_24h} ({risk_24h:.2f}), "
        f"current risk={risk_score:.2f}, predictive_only={int(predictive_only)}."
    )

    model_context = _model_context(manifest_payload, manifest_path)
    evidence_context_csv = str(latest.get("alert_context_csv", "")).strip()

    payload = {
        "workflow_type": "elevator_predictive_maintenance_v1",
        "generated_at_ms": int(time.time() * 1000),
        "site_name": site_name or "unknown-site",
        "status": status,
        "elevator_id": elevator_id,
        "priority": priority,
        "maintenance_mode": maintenance_mode,
        "dispatch_within_hours": dispatch_hours,
        "summary": summary,
        "current_fault_type": fault_type,
        "current_fault_confidence": fault_confidence,
        "predictive_only": predictive_only,
        "risk": {
            "risk_score": risk_score,
            "risk_level_now": str(latest.get("risk_level_now") or health.get("last_risk_level_now") or "normal"),
            "risk_24h": risk_24h,
            "risk_level_24h": risk_level_24h,
            "degradation_slope": degradation_slope,
        },
        "recent_alert_stats": {
            "recent_alert_count": len(alert_rows),
            "top_faults": top_faults,
            "last_alert_ts_ms": _safe_int(latest.get("ts_ms")),
            "last_alert_level": level,
        },
        "monitor": {
            "runtime_status": str(health.get("status", "unknown")),
            "connected": bool(health.get("connected", False)),
            "alerts_emitted": _safe_int(health.get("alerts_emitted"), len(alert_rows)),
            "records_written": _safe_int(health.get("records_written")),
            "baseline_ready": bool(health.get("baseline_ready", False)),
        },
        "recommended_actions": actions,
        "suggested_parts": parts,
        "evidence": {
            "alert_csv": alert_csv_path,
            "health_json": health_json_path,
            "alert_context_csv": evidence_context_csv,
        },
        "model_context": model_context,
    }

    payload["dify_inputs"] = {
        "ticket_title": f"{elevator_id} {priority} {fault_type}",
        "ticket_priority": priority,
        "site_name": payload["site_name"],
        "elevator_id": elevator_id,
        "maintenance_mode": maintenance_mode,
        "dispatch_within_hours": dispatch_hours,
        "status": status,
        "fault_type": fault_type,
        "fault_confidence": fault_confidence,
        "risk_level_now": payload["risk"]["risk_level_now"],
        "risk_level_24h": risk_level_24h,
        "risk_24h": risk_24h,
        "predictive_only": predictive_only,
        "summary": summary,
        "recommended_actions_text": " | ".join(actions),
        "suggested_parts_text": ", ".join(parts),
        "alert_context_csv": evidence_context_csv,
        "model_ids": payload["model_context"]["model_ids"],
    }
    return payload


def render_markdown(package: dict[str, Any]) -> str:
    risk = package.get("risk", {})
    stats = package.get("recent_alert_stats", {})
    actions = package.get("recommended_actions", [])
    parts = package.get("suggested_parts", [])
    evidence = package.get("evidence", {})

    lines = [
        "# Predictive Maintenance Package",
        "",
        f"- Site: {package.get('site_name', '')}",
        f"- Elevator: {package.get('elevator_id', '')}",
        f"- Priority: {package.get('priority', '')}",
        f"- Mode: {package.get('maintenance_mode', '')}",
        f"- Dispatch within: {package.get('dispatch_within_hours', 0)}h",
        f"- Fault: {package.get('current_fault_type', '')} ({_safe_float(package.get('current_fault_confidence'), 0.0):.2f})",
        f"- Risk now: {_safe_float(risk.get('risk_score'), 0.0):.2f} / {risk.get('risk_level_now', '')}",
        f"- Risk 24h: {_safe_float(risk.get('risk_24h'), 0.0):.2f} / {risk.get('risk_level_24h', '')}",
        f"- Recent alerts: {_safe_int(stats.get('recent_alert_count'), 0)}",
        "",
        "## Summary",
        package.get("summary", ""),
        "",
        "## Actions",
    ]

    if actions:
        for item in actions:
            lines.append(f"- {item}")
    else:
        lines.append("- Continue observation and collect more labeled data.")

    lines.extend(["", "## Parts"])
    if parts:
        for item in parts:
            lines.append(f"- {item}")
    else:
        lines.append("- No mandatory spare parts suggested yet.")

    lines.extend(
        [
            "",
            "## Evidence",
            f"- Alert CSV: {evidence.get('alert_csv', '')}",
            f"- Health JSON: {evidence.get('health_json', '')}",
            f"- Alert Context CSV: {evidence.get('alert_context_csv', '')}",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a maintenance package from monitor outputs")
    parser.add_argument("--alert-csv", default="data/elevator_alerts_live.csv", help="alert csv generated by realtime monitor")
    parser.add_argument("--health-json", default="data/monitor_health.json", help="health json generated by realtime monitor")
    parser.add_argument("--manifest-json", default="", help="optional model manifest json")
    parser.add_argument("--site-name", default="", help="site or building name")
    parser.add_argument("--recent-alert-limit", type=int, default=50, help="number of recent alerts to summarize")
    parser.add_argument("--output-json", default="", help="optional output json path")
    parser.add_argument("--output-md", default="", help="optional output markdown path")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()

    alert_rows = load_recent_alerts(args.alert_csv, limit=max(1, args.recent_alert_limit))
    health_payload = load_optional_json(args.health_json)
    manifest_payload = load_optional_json(args.manifest_json)

    package = build_maintenance_package(
        alert_rows=alert_rows,
        health_payload=health_payload,
        site_name=args.site_name,
        alert_csv_path=args.alert_csv,
        health_json_path=args.health_json,
        manifest_payload=manifest_payload,
        manifest_path=args.manifest_json,
    )
    markdown = render_markdown(package)

    if args.output_json:
        out_json = Path(args.output_json)
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.output_md:
        out_md = Path(args.output_md)
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(markdown, encoding="utf-8")

    if not args.output_json and not args.output_md:
        print(json.dumps(package, ensure_ascii=False, indent=2))
    else:
        print(f"priority={package['priority']} mode={package['maintenance_mode']} elevator_id={package['elevator_id']}")
        if args.output_json:
            print(f"json={args.output_json}")
        if args.output_md:
            print(f"markdown={args.output_md}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
