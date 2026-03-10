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
            "rule_diagnosis",
            "waveform_plot",
            "maintenance_package",
            "diagnosis_report",
            "monitor_health",
        ],
    }
