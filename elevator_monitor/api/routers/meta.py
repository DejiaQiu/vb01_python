from __future__ import annotations

from typing import Any

from fastapi import APIRouter


router = APIRouter(prefix="/api/v1", tags=["meta"])


@router.get("/meta")
def meta() -> dict[str, Any]:
    return {
        "service": "elevator-monitor-api",
        "version": "1.0.0",
        "capabilities": [
            "ingest_heartbeat",
            "ingest_alert",
            "ingest_context",
            "rule_diagnosis",
            "batch_diagnosis",
            "latest_status",
            "edge_latest_status",
            "edge_alerts",
            "waveform_plot",
            "maintenance_package",
            "diagnosis_report",
            "diagnosis_report_by_event",
            "monitor_health",
        ],
    }
