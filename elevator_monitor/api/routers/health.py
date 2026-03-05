from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ...healthcheck import check_health
from ...maintenance_workflow import load_optional_json


router = APIRouter(prefix="/api/v1/health", tags=["health"])


@router.get("/monitor")
def monitor_health(health_path: str = "data/monitor_health.json", max_age_s: float = 30.0) -> dict[str, Any]:
    from pathlib import Path

    path = Path(health_path).expanduser().resolve()
    ok, message = check_health(path, max(1.0, max_age_s))
    payload = load_optional_json(str(path))
    return {
        "ok": ok,
        "message": message,
        "health_path": str(path),
        "payload": payload,
    }
