from __future__ import annotations

from typing import Any

from ..data_recorder import format_ts_ms


ALERT_FIELDS = [
    "elevator_id",
    "ts_ms",
    "ts",
    "level",
    "anomaly_level",
    "predictive_only",
    "score",
    "reasons",
    "fault_type",
    "fault_confidence",
    "fault_source",
    "fault_candidates",
    "fault_reasons",
    "fault_screening_status",
    "fault_primary_score",
    "fault_detector_family",
    "fault_attribution_margin",
    "system_score",
    "system_gate_mode",
    "baseline_mode",
    "baseline_match",
    "sampling_condition",
    "axis_mapping_signature",
    "risk_score",
    "risk_level_now",
    "risk_24h",
    "risk_level_24h",
    "degradation_slope",
    "risk_reasons",
    "alert_context_path",
    "alert_context_csv",
    "records_written",
    "skipped_total",
    "baseline_ready",
    "baseline_size",
    "stale_repeat",
]

RISK_LEVEL_RANK = {
    "normal": 0,
    "watch": 1,
    "high": 2,
    "critical": 3,
}


def should_emit_predictive_alert(
    risk_level_24h: str,
    risk_emit_on_normal: bool,
    risk_emit_min_level: str,
) -> bool:
    if not risk_emit_on_normal:
        return False
    current_rank = RISK_LEVEL_RANK.get(risk_level_24h, 0)
    min_rank = RISK_LEVEL_RANK.get(risk_emit_min_level, 2)
    return current_rank >= min_rank


def build_alert_record(
    *,
    elevator_id: str,
    ts_ms: int,
    level: str,
    anomaly_level: str,
    predictive_only: bool,
    anomaly_result: dict[str, Any],
    fault_result: dict[str, Any],
    risk_result: dict[str, Any],
    alert_context_path: str,
    records_written: int,
    skipped_total: int,
) -> dict[str, Any]:
    reasons = list(anomaly_result.get("reasons", []))
    if predictive_only:
        reasons.append("predictive_risk")

    return {
        "elevator_id": elevator_id,
        "ts_ms": ts_ms,
        "ts": format_ts_ms(ts_ms),
        "level": level,
        "anomaly_level": anomaly_level,
        "predictive_only": int(predictive_only),
        "score": f"{float(anomaly_result.get('score', 0.0)):.4f}",
        "reasons": "|".join(reasons),
        "fault_type": fault_result.get("fault_type", "unknown"),
        "fault_confidence": f"{float(fault_result.get('fault_confidence', 0.0)):.4f}",
        "fault_source": fault_result.get("fault_source", ""),
        "fault_candidates": fault_result.get("fault_candidates", ""),
        "fault_reasons": fault_result.get("fault_reasons", ""),
        "fault_screening_status": fault_result.get("fault_screening_status", "normal"),
        "fault_primary_score": f"{float(fault_result.get('fault_primary_score', 0.0)):.4f}",
        "fault_detector_family": fault_result.get("fault_detector_family", ""),
        "fault_attribution_margin": f"{float(fault_result.get('fault_attribution_margin', 0.0)):.4f}",
        "system_score": f"{float(fault_result.get('system_score', 0.0)):.4f}",
        "system_gate_mode": fault_result.get("system_gate_mode", ""),
        "baseline_mode": fault_result.get("baseline_mode", ""),
        "baseline_match": fault_result.get("baseline_match"),
        "sampling_condition": fault_result.get("sampling_condition", ""),
        "axis_mapping_signature": fault_result.get("axis_mapping_signature", ""),
        "risk_score": f"{float(risk_result.get('risk_score', 0.0)):.4f}",
        "risk_level_now": risk_result.get("risk_level_now", "normal"),
        "risk_24h": f"{float(risk_result.get('risk_24h', 0.0)):.4f}",
        "risk_level_24h": risk_result.get("risk_level_24h", "normal"),
        "degradation_slope": f"{float(risk_result.get('degradation_slope', 0.0)):.6f}",
        "risk_reasons": risk_result.get("risk_reasons", ""),
        "alert_context_path": alert_context_path,
        "alert_context_csv": alert_context_path,
        "records_written": records_written,
        "skipped_total": skipped_total,
        "baseline_ready": anomaly_result.get("baseline_ready"),
        "baseline_size": anomaly_result.get("baseline_count"),
        "stale_repeat": anomaly_result.get("stale_repeat"),
    }
