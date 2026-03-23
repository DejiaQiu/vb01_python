from __future__ import annotations

import re
from typing import Any
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ...batch_diagnosis import load_latest_status, run_batch_diagnosis
from ...waveform_service import build_waveform_payload, load_waveform_rows
from report.fault_algorithms.run_all import run_all, run_all_rows

from ..schemas import BatchDiagnosisRequest, RuleDiagnosisRequest, WaveformPlotRequest, resolve_rule_rows


router = APIRouter(prefix="/api/v1/diagnostics", tags=["diagnostics"])
_DIGITS_ONLY = re.compile(r"^\d+$")


def _elevator_path_tokens(elevator_id: str) -> list[str]:
    raw = str(elevator_id or "").strip()
    if not raw:
        return []
    lowered = raw.lower()
    suffix = re.sub(r"^elevator[_-]?", "", lowered)
    tokens: list[str] = []

    def _push(value: str) -> None:
        value = str(value).strip()
        if value and value not in tokens:
            tokens.append(value)

    _push(lowered)
    _push(f"elevator_{suffix}")
    _push(f"elevator-{suffix}")
    if _DIGITS_ONLY.fullmatch(suffix):
        padded = f"{int(suffix):03d}"
        _push(padded)
        _push(f"elevator_{padded}")
        _push(f"elevator-{padded}")
    return tokens


def _resolve_latest_status_path(latest_json: str, elevator_id: str, latest_root: str) -> Path:
    if str(elevator_id or "").strip():
        root = Path(latest_root).expanduser().resolve()
        candidates: list[Path] = []
        for token in _elevator_path_tokens(elevator_id):
            candidates.append(root / token / "latest_status.json")
        for path in candidates:
            if path.exists():
                return path
        if candidates:
            return candidates[0]
    return Path(latest_json).expanduser().resolve()


def _attach_latest_waveforms(
    payload: dict[str, Any],
    *,
    width: int,
    height: int,
    max_points: int,
) -> dict[str, Any]:
    enriched = dict(payload)
    latest_file = str(enriched.get("latest_file", "")).strip()
    if not latest_file:
        enriched["waveform_payload"] = {}
        enriched["waveform_error"] = "latest_file missing in latest status payload"
        return enriched

    try:
        rows, source = load_waveform_rows([], "", latest_file)
    except FileNotFoundError as exc:
        enriched["waveform_payload"] = {}
        enriched["waveform_error"] = str(exc)
        return enriched
    except ValueError as exc:
        enriched["waveform_payload"] = {}
        enriched["waveform_error"] = str(exc)
        return enriched

    if not rows:
        enriched["waveform_payload"] = {}
        enriched["waveform_error"] = "no usable rows found"
        return enriched

    enriched["waveform_payload"] = build_waveform_payload(
        rows,
        source=source,
        width=max(320, int(width)),
        height=max(180, int(height)),
        max_points=max(32, int(max_points)),
        diagnosis_result=payload,
    )
    return enriched


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


@router.post("/batch-run")
def batch_run(request: BatchDiagnosisRequest) -> dict[str, Any]:
    try:
        return run_batch_diagnosis(
            input_dir=request.input_dir,
            csv_paths=list(request.csv_paths),
            max_files=max(1, int(request.max_files)),
            baseline_json=request.baseline_json,
            baseline_dir=request.baseline_dir,
            baseline_start_hhmm=request.baseline_start_hhmm,
            baseline_end_hhmm=request.baseline_end_hhmm,
            latest_json=request.latest_json,
            history_jsonl=request.history_jsonl,
            write_outputs=bool(request.write_outputs),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/latest-status")
def latest_status(
    latest_json: str = "data/diagnosis/latest_status.json",
    elevator_id: str = "",
    latest_root: str = "data/diagnosis",
    include_waveforms: bool = False,
    waveform_width: int = 920,
    waveform_height: int = 320,
    waveform_max_points: int = 240,
) -> dict[str, Any]:
    resolved_latest = _resolve_latest_status_path(latest_json, elevator_id, latest_root)
    try:
        payload = load_latest_status(str(resolved_latest))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    payload = dict(payload)
    payload["latest_json"] = str(resolved_latest)
    payload["requested_elevator_id"] = str(elevator_id or "").strip()
    if include_waveforms:
        payload = _attach_latest_waveforms(
            payload,
            width=waveform_width,
            height=waveform_height,
            max_points=waveform_max_points,
        )
    return payload
