from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field


class RuleDiagnosisRequest(BaseModel):
    csv_path: str = ""
    csv_text: str = ""
    rows: list[dict[str, Any]] = Field(default_factory=list)


class WaveformPlotRequest(BaseModel):
    csv_path: str = ""
    csv_text: str = ""
    rows: list[dict[str, Any]] = Field(default_factory=list)
    width: int = 920
    height: int = 320
    max_points: int = 240


class MaintenancePackageRequest(BaseModel):
    site_name: str = ""
    alert_csv: str = "data/elevator_alerts_live.csv"
    health_json: str = "data/monitor_health.json"
    manifest_json: str = ""
    recent_alert_limit: int = 50
    alert_rows: list[dict[str, Any]] = Field(default_factory=list)
    health_payload: dict[str, Any] = Field(default_factory=dict)
    manifest_payload: dict[str, Any] = Field(default_factory=dict)


class DiagnosisReportRequest(BaseModel):
    site_name: str = ""
    csv_path: str = ""
    csv_text: str = ""
    rows: list[dict[str, Any]] = Field(default_factory=list)
    diagnosis_result: dict[str, Any] = Field(default_factory=dict)
    maintenance_package: dict[str, Any] = Field(default_factory=dict)
    alert_csv: str = "data/elevator_alerts_live.csv"
    health_json: str = "data/monitor_health.json"
    manifest_json: str = ""
    recent_alert_limit: int = 50
    language: str = "zh-CN"
    report_style: str = "standard"
    include_waveforms: bool = False
    waveform_payload: dict[str, Any] = Field(default_factory=dict)


class BatchDiagnosisRequest(BaseModel):
    input_dir: str = ""
    csv_paths: list[str] = Field(default_factory=list)
    max_files: int = 12
    baseline_json: str = ""
    baseline_dir: str = ""
    baseline_start_hhmm: str = "0000"
    baseline_end_hhmm: str = "2359"
    latest_json: str = "data/diagnosis/latest_status.json"
    history_jsonl: str = "data/diagnosis/history.jsonl"
    write_outputs: bool = True


def normalize_row_values(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        normalized.append({str(key): "" if value is None else str(value) for key, value in row.items()})
    return normalized


def rows_from_csv_text(csv_text: str) -> list[dict[str, str]]:
    text = csv_text.strip()
    if not text:
        return []
    return [dict(row) for row in csv.DictReader(io.StringIO(text))]


def resolve_rule_rows_raw(
    rows: list[dict[str, Any]],
    csv_text: str,
    csv_path: str,
) -> tuple[list[dict[str, str]], str]:
    if rows:
        return normalize_row_values(rows), "inline_rows"

    if csv_text.strip():
        parsed_rows = rows_from_csv_text(csv_text)
        return parsed_rows, "inline_csv_text"

    if csv_path.strip():
        path = Path(csv_path).expanduser().resolve()
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"csv not found: {path}")
        return [], str(path)

    raise HTTPException(status_code=400, detail="provide rows, csv_text, or csv_path")


def resolve_rule_rows(payload: RuleDiagnosisRequest) -> tuple[list[dict[str, str]], str]:
    return resolve_rule_rows_raw(payload.rows, payload.csv_text, payload.csv_path)
