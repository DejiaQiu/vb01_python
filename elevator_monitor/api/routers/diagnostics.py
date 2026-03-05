from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from report.fault_algorithms.run_all import run_all, run_all_rows

from ..schemas import RuleDiagnosisRequest, resolve_rule_rows


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
