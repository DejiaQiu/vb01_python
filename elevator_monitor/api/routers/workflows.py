from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ...maintenance_workflow import build_maintenance_package, load_optional_json, load_recent_alerts
from ...reporting_service import build_report_context, render_report_markdown
from report.fault_algorithms.run_all import run_all, run_all_rows
from ..schemas import (
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

    report_ctx = build_report_context(
        diagnosis_result=diagnosis,
        maintenance_package=package,
        language=request.language,
        report_style=request.report_style,
    )
    report_ctx["report_markdown_draft"] = render_report_markdown(report_ctx)
    return report_ctx
