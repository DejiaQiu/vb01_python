from __future__ import annotations

from typing import Any, Optional

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
    "fault_model_pred",
    "fault_model_confidence",
    "fault_model_top_k",
    "fault_generated_pred",
    "fault_generated_confidence",
    "fault_generated_top_k",
    "forecast_a_mag",
    "forecast_g_mag",
    "forecast_t",
    "forecast_confidence",
    "risk_score",
    "risk_level_now",
    "risk_24h",
    "risk_level_24h",
    "degradation_slope",
    "risk_reasons",
    "risk_model_score",
    "alert_context_csv",
    "dify_dispatched",
    "dify_status",
    "dify_workflow_run_id",
    "dify_task_id",
    "dify_error",
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
    alert_context_csv: str,
    dify_result: Optional[dict[str, Any]],
    records_written: int,
    skipped_total: int,
) -> dict[str, Any]:
    reasons = list(anomaly_result.get("reasons", []))
    if predictive_only:
        reasons.append("predictive_risk")
    dify = dify_result or {}

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
        "fault_model_pred": fault_result.get("fault_model_pred", ""),
        "fault_model_confidence": f"{float(fault_result.get('fault_model_confidence', 0.0)):.4f}",
        "fault_model_top_k": fault_result.get("fault_model_top_k", ""),
        "fault_generated_pred": fault_result.get("fault_generated_pred", ""),
        "fault_generated_confidence": f"{float(fault_result.get('fault_generated_confidence', 0.0)):.4f}",
        "fault_generated_top_k": fault_result.get("fault_generated_top_k", ""),
        "forecast_a_mag": f"{float(fault_result.get('forecast_a_mag', 0.0)):.4f}",
        "forecast_g_mag": f"{float(fault_result.get('forecast_g_mag', 0.0)):.4f}",
        "forecast_t": f"{float(fault_result.get('forecast_t', 0.0)):.4f}",
        "forecast_confidence": f"{float(fault_result.get('forecast_confidence', 0.0)):.4f}",
        "risk_score": f"{float(risk_result.get('risk_score', 0.0)):.4f}",
        "risk_level_now": risk_result.get("risk_level_now", "normal"),
        "risk_24h": f"{float(risk_result.get('risk_24h', 0.0)):.4f}",
        "risk_level_24h": risk_result.get("risk_level_24h", "normal"),
        "degradation_slope": f"{float(risk_result.get('degradation_slope', 0.0)):.6f}",
        "risk_reasons": risk_result.get("risk_reasons", ""),
        "risk_model_score": f"{float(risk_result.get('risk_model_score', 0.0)):.4f}",
        "alert_context_csv": alert_context_csv,
        "dify_dispatched": int(dify.get("dify_dispatched", 0)),
        "dify_status": dify.get("dify_status", ""),
        "dify_workflow_run_id": dify.get("dify_workflow_run_id", ""),
        "dify_task_id": dify.get("dify_task_id", ""),
        "dify_error": dify.get("dify_error", ""),
        "records_written": records_written,
        "skipped_total": skipped_total,
        "baseline_ready": anomaly_result.get("baseline_ready"),
        "baseline_size": anomaly_result.get("baseline_count"),
        "stale_repeat": anomaly_result.get("stale_repeat"),
    }
