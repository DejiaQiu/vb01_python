from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ...waveform_service import build_waveform_payload, load_waveform_rows
from report.fault_algorithms.run_all import run_all, run_all_rows

from ..schemas import RuleDiagnosisRequest, WaveformPlotRequest, resolve_rule_rows


router = APIRouter(prefix="/api/v1/diagnostics", tags=["diagnostics"])


@router.post("/rule-engine")
def diagnose_rule_engine(request: RuleDiagnosisRequest) -> dict[str, Any]:
    from pathlib import Path

    rows, source = resolve_rule_rows(request)
    if not request.rows and not request.csv_text.strip() and request.csv_path.strip():
        path = Path(request.csv_path).expanduser().resolve()
        return run_all(path)

    if not rows:
        raise HTTPException(status_code=400, detail="no usable rows found")
    return run_all_rows(rows, source=source)


@router.post("/waveform-plot")
def waveform_plot(request: WaveformPlotRequest) -> dict[str, Any]:
    try:
        rows, source = load_waveform_rows(request.rows, request.csv_text, request.csv_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if not rows:
        raise HTTPException(status_code=400, detail="no usable rows found")

    return build_waveform_payload(
        rows,
        source=source,
        width=max(320, int(request.width)),
        height=max(180, int(request.height)),
        max_points=max(32, int(request.max_points)),
    )
