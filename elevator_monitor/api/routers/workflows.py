from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from ...batch_diagnosis import load_latest_status
from ...ingest_store import get_ingest_store
from ...latest_status_service import attach_latest_waveforms, resolve_latest_status_path
from ...maintenance_workflow import build_maintenance_package, load_optional_json, load_recent_alerts
from ...reporting_service import (
    build_report_context,
    build_report_context_from_edge_event,
    build_report_context_from_latest_status,
    render_report_markdown,
)
from ...waveform_service import build_waveform_payload, load_waveform_rows
from report.fault_algorithms.run_all import run_all, run_all_rows
from ..schemas import (
    DiagnosisReportByEventRequest,
    DiagnosisReportLatestRequest,
    DiagnosisReportRequest,
    MaintenancePackageRequest,
    normalize_row_values,
    resolve_rule_rows_raw,
)


router = APIRouter(prefix="/api/v1/workflows", tags=["workflows"])


@router.post("/maintenance-package")
def maintenance_package(request: MaintenancePackageRequest) -> dict[str, Any]:
    alert_rows = normalize_row_values(request.alert_rows) if request.alert_rows else load_recent_alerts(
        request.alert_csv,
        limit=max(1, request.recent_alert_limit),
    )
    health_payload = dict(request.health_payload) if request.health_payload else load_optional_json(request.health_json)
    manifest_payload = dict(request.manifest_payload) if request.manifest_payload else load_optional_json(request.manifest_json)

    package = build_maintenance_package(
        alert_rows=alert_rows,
        health_payload=health_payload,
        site_name=request.site_name,
        alert_csv_path=request.alert_csv,
        health_json_path=request.health_json,
        manifest_payload=manifest_payload,
        manifest_path=request.manifest_json,
    )
    return package


@router.post("/diagnosis-report")
def diagnosis_report(request: DiagnosisReportRequest) -> dict[str, Any]:
    diagnosis = dict(request.diagnosis_result) if request.diagnosis_result else {}
    if not diagnosis:
        rows, _source = resolve_rule_rows_raw(
            request.rows,
            request.csv_text,
            request.csv_path,
        )
        if request.csv_path.strip() and not request.rows and not request.csv_text.strip():
            from pathlib import Path

            diagnosis = run_all(Path(request.csv_path).expanduser().resolve())
        else:
            diagnosis = run_all_rows(rows, source="report_inline")

    package = dict(request.maintenance_package) if request.maintenance_package else {}
    if not package:
        alert_rows = load_recent_alerts(request.alert_csv, limit=max(1, request.recent_alert_limit))
        health_payload = load_optional_json(request.health_json)
        manifest_payload = load_optional_json(request.manifest_json)
        package = build_maintenance_package(
            alert_rows=alert_rows,
            health_payload=health_payload,
            site_name=request.site_name,
            alert_csv_path=request.alert_csv,
            health_json_path=request.health_json,
            manifest_payload=manifest_payload,
            manifest_path=request.manifest_json,
        )

    waveform_payload = dict(request.waveform_payload) if request.waveform_payload else {}
    if request.include_waveforms and not waveform_payload:
        try:
            rows, source = load_waveform_rows(request.rows, request.csv_text, request.csv_path)
        except (ValueError, FileNotFoundError):
            rows, source = [], ""
        if rows:
            waveform_payload = build_waveform_payload(rows, source=source, diagnosis_result=diagnosis)

    report_ctx = build_report_context(
        diagnosis_result=diagnosis,
        maintenance_package=package,
        language=request.language,
        report_style=request.report_style,
        waveform_payload=waveform_payload,
    )
    report_ctx["report_markdown_draft"] = render_report_markdown(report_ctx)
    return report_ctx


def _build_latest_report_context(request: DiagnosisReportLatestRequest) -> dict[str, Any]:
    latest_payload = _load_latest_payload(request, include_waveforms=request.include_waveforms)
    resolved_latest = str(latest_payload.get("latest_json", request.latest_json))

    report_ctx = build_report_context_from_latest_status(
        latest_status_payload=latest_payload,
        elevator_id=request.elevator_id,
        site_name=request.site_name,
        language=request.language,
        report_style=request.report_style,
        waveform_payload=dict(latest_payload.get("waveform_payload", {}))
        if isinstance(latest_payload.get("waveform_payload"), dict)
        else {},
    )
    report_ctx["workflow_type"] = str(latest_payload.get("workflow_type", "scheduled_batch_diagnosis_v1"))
    report_ctx["generated_at_ms"] = int(
        latest_payload.get("generated_at_ms", report_ctx.get("generated_at_ms", 0))
        or report_ctx.get("generated_at_ms", 0)
    )
    report_ctx["status"] = str(
        (report_ctx.get("screening", {}) if isinstance(report_ctx.get("screening"), dict) else {}).get(
            "status", latest_payload.get("status", "normal")
        )
    )
    report_ctx["primary_issue"] = dict(latest_payload.get("primary_issue", {})) if isinstance(
        latest_payload.get("primary_issue"), dict
    ) else {}
    if not report_ctx["primary_issue"] and isinstance(report_ctx.get("preferred_issue"), dict):
        report_ctx["primary_issue"] = {
            "fault_type": str(report_ctx["preferred_issue"].get("fault_type", "unknown")),
            "score": report_ctx["preferred_issue"].get("score", 0.0),
            "level": str(report_ctx["preferred_issue"].get("level", "normal")),
        }
    report_ctx["top_candidate"] = dict(latest_payload.get("top_candidate", {})) if isinstance(
        latest_payload.get("top_candidate"), dict
    ) else {}
    report_ctx["watch_faults"] = [
        dict(item) for item in latest_payload.get("watch_faults", []) if isinstance(item, dict)
    ]
    report_ctx["auxiliary_results"] = [
        dict(item) for item in latest_payload.get("auxiliary_results", []) if isinstance(item, dict)
    ]
    report_ctx["recommendation"] = str(latest_payload.get("recommendation", "")).strip()
    report_ctx["latest_file"] = str(latest_payload.get("latest_file", "")).strip()
    report_ctx["latest_file_name"] = str(latest_payload.get("latest_file_name", "")).strip()
    report_ctx["latest_json"] = resolved_latest
    report_ctx["requested_elevator_id"] = str(request.elevator_id or "").strip()
    report_ctx["report_markdown_draft"] = render_report_markdown(report_ctx)
    return report_ctx


def _load_latest_payload(
    request: DiagnosisReportLatestRequest,
    *,
    include_waveforms: bool,
) -> dict[str, Any]:
    resolved_latest = resolve_latest_status_path(request.latest_json, request.elevator_id, request.latest_root)
    try:
        latest_payload = load_latest_status(str(resolved_latest))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    latest_payload = dict(latest_payload)
    latest_payload["latest_json"] = str(resolved_latest)
    latest_payload["requested_elevator_id"] = str(request.elevator_id or "").strip()
    if include_waveforms:
        latest_payload = attach_latest_waveforms(
            latest_payload,
            width=request.waveform_width,
            height=request.waveform_height,
            max_points=request.waveform_max_points,
        )
    return latest_payload


@router.get("/diagnosis-report-latest")
def diagnosis_report_latest(
    elevator_id: str = "",
    site_name: str = "",
    latest_json: str = "data/diagnosis/latest_status.json",
    latest_root: str = "data/diagnosis",
    language: str = "zh-CN",
    report_style: str = "standard",
    include_waveforms: bool = True,
    waveform_width: int = 920,
    waveform_height: int = 320,
    waveform_max_points: int = 240,
) -> dict[str, Any]:
    request = DiagnosisReportLatestRequest(
        elevator_id=elevator_id,
        site_name=site_name,
        latest_json=latest_json,
        latest_root=latest_root,
        language=language,
        report_style=report_style,
        include_waveforms=include_waveforms,
        waveform_width=waveform_width,
        waveform_height=waveform_height,
        waveform_max_points=waveform_max_points,
    )
    return _build_latest_report_context(request)


def _build_latest_request_from_raw(
    *,
    payload: dict[str, Any] | None = None,
    query_params: dict[str, Any] | None = None,
) -> DiagnosisReportLatestRequest:
    body = dict(payload or {})
    query = dict(query_params or {})
    merged: dict[str, Any] = {}
    for key in (
        "elevator_id",
        "site_name",
        "latest_json",
        "latest_root",
        "language",
        "report_style",
        "include_waveforms",
        "waveform_width",
        "waveform_height",
        "waveform_max_points",
    ):
        if key in body and body.get(key) not in (None, ""):
            merged[key] = body.get(key)
        elif key in query and query.get(key) not in (None, ""):
            merged[key] = query.get(key)
    return DiagnosisReportLatestRequest(**merged)


@router.post("/diagnosis-report-latest")
async def diagnosis_report_latest_post(request: Request) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    raw_body = await request.body()
    if raw_body.strip():
        try:
            parsed = await request.json()
        except Exception:
            try:
                parsed = json.loads(raw_body.decode("utf-8", errors="replace"))
            except Exception:
                parsed = {}
        if isinstance(parsed, dict):
            payload = dict(parsed)
    model = _build_latest_request_from_raw(
        payload=payload,
        query_params=dict(request.query_params),
    )
    return _build_latest_report_context(model)


@router.get("/diagnosis-report-latest/plot")
def diagnosis_report_latest_plot(
    kind: str,
    elevator_id: str = "",
    latest_json: str = "data/diagnosis/latest_status.json",
    latest_root: str = "data/diagnosis",
    waveform_width: int = 920,
    waveform_height: int = 320,
    waveform_max_points: int = 240,
) -> Response:
    supported_kinds = {
        "full_frequency_spectrum",
        "low_frequency_spectrum",
        "acceleration",
        "gyroscope",
        "acceleration_magnitude",
    }
    kind_text = str(kind or "").strip()
    if kind_text not in supported_kinds:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported plot kind: {kind_text or 'empty'}",
        )

    request_model = DiagnosisReportLatestRequest(
        elevator_id=elevator_id,
        latest_json=latest_json,
        latest_root=latest_root,
        include_waveforms=True,
        waveform_width=waveform_width,
        waveform_height=waveform_height,
        waveform_max_points=waveform_max_points,
    )
    latest_payload = _load_latest_payload(request_model, include_waveforms=True)
    waveform_payload = latest_payload.get("waveform_payload", {})
    plots = waveform_payload.get("plots", {}) if isinstance(waveform_payload, dict) else {}
    plot = plots.get(kind_text, {}) if isinstance(plots, dict) else {}
    svg = str(plot.get("svg", "")).strip() if isinstance(plot, dict) else ""
    if not svg:
        raise HTTPException(status_code=404, detail=f"plot not available: {kind_text}")
    return Response(content=svg, media_type="image/svg+xml")


@router.post("/diagnosis-report-by-event")
def diagnosis_report_by_event(request: DiagnosisReportByEventRequest) -> dict[str, Any]:
    event_id = request.event_id.strip()
    if not event_id:
        raise HTTPException(status_code=400, detail="event_id is required")

    store = get_ingest_store()
    event_payload = store.get_alert(event_id)
    if not event_payload:
        raise HTTPException(status_code=404, detail=f"alert event not found: {event_id}")

    report_input = dict(event_payload)
    if request.site_name.strip():
        report_input["site_name"] = request.site_name.strip()

    report_ctx = build_report_context_from_edge_event(
        alert_event=report_input,
        language=request.language,
        report_style=request.report_style,
        include_waveforms=request.include_waveforms,
    )
    report_ctx["event_id"] = event_id
    report_ctx["alert_event"] = dict(event_payload)
    report_ctx["report_markdown_draft"] = render_report_markdown(report_ctx)
    return report_ctx
