from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ...ingest_store import get_ingest_store
from ..schemas import IngestAlertRequest, IngestContextRequest, IngestHeartbeatRequest


router = APIRouter(tags=["ingest"])


@router.post("/api/v1/ingest/heartbeat")
def ingest_heartbeat(request: IngestHeartbeatRequest) -> dict[str, Any]:
    try:
        return get_ingest_store().record_heartbeat(request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/v1/ingest/alert")
def ingest_alert(request: IngestAlertRequest) -> dict[str, Any]:
    try:
        return get_ingest_store().record_alert(request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/v1/ingest/context")
def ingest_context(request: IngestContextRequest) -> dict[str, Any]:
    try:
        return get_ingest_store().record_context(request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/v1/elevators/{elevator_id}/latest-status")
def elevator_latest_status(elevator_id: str) -> dict[str, Any]:
    payload = get_ingest_store().get_latest_status(elevator_id)
    if not payload:
        raise HTTPException(status_code=404, detail=f"latest status not found: {elevator_id}")
    payload = dict(payload)
    payload["query_elevator_id"] = elevator_id
    return payload


@router.get("/api/v1/elevators/{elevator_id}/alerts")
def elevator_alerts(elevator_id: str, limit: int = 20) -> dict[str, Any]:
    items = get_ingest_store().list_alerts(elevator_id, limit=max(1, int(limit)))
    return {
        "elevator_id": elevator_id,
        "count": len(items),
        "items": items,
    }


@router.get("/api/v1/alerts/{event_id}")
def alert_detail(event_id: str) -> dict[str, Any]:
    payload = get_ingest_store().get_alert(event_id)
    if not payload:
        raise HTTPException(status_code=404, detail=f"alert not found: {event_id}")
    payload = dict(payload)
    payload["event_id"] = event_id
    return payload
