from __future__ import annotations

import csv
import gzip
import io
import re
from pathlib import Path
from typing import Any

from .waveform_service import build_waveform_payload, load_waveform_rows


_DIGITS_ONLY = re.compile(r"^\d+$")


def elevator_path_tokens(elevator_id: str) -> list[str]:
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


def resolve_latest_status_path(latest_json: str, elevator_id: str, latest_root: str) -> Path:
    if str(elevator_id or "").strip():
        root = Path(latest_root).expanduser().resolve()
        candidates: list[Path] = []
        for token in elevator_path_tokens(elevator_id):
            candidates.append(root / token / "latest_status.json")
        for path in candidates:
            if path.exists():
                return path
        if candidates:
            return candidates[0]
    return Path(latest_json).expanduser().resolve()


def _load_rows_from_context(context: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    stored_path = str(context.get("stored_path", "")).strip() or str(context.get("local_path", "")).strip()
    if not stored_path:
        raise ValueError("waveform context path missing in latest status payload")
    path = Path(stored_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"waveform context not found: {path}")
    compression = str(context.get("compression", "")).strip().lower()
    if compression == "gzip" or path.suffix.lower() == ".gz":
        text = gzip.decompress(path.read_bytes()).decode("utf-8", errors="replace")
        rows = [dict(row) for row in csv.DictReader(io.StringIO(text))]
        return rows, str(path)
    return load_waveform_rows([], "", str(path))


def _load_latest_waveform_rows(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], str, str]:
    latest_file = str(payload.get("latest_file", "")).strip()
    latest_file_name = str(payload.get("latest_file_name", "")).strip()
    if latest_file:
        rows, source = load_waveform_rows([], "", latest_file)
        return rows, source, latest_file_name or Path(source).name

    context = payload.get("context", {}) if isinstance(payload.get("context"), dict) else {}
    rows, source = _load_rows_from_context(context)
    return rows, source, str(context.get("file_name", "")).strip() or Path(source).name


def attach_latest_waveforms(
    payload: dict[str, Any],
    *,
    width: int,
    height: int,
    max_points: int,
) -> dict[str, Any]:
    enriched = dict(payload)
    try:
        rows, source, source_name = _load_latest_waveform_rows(enriched)
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

    if not str(enriched.get("latest_file", "")).strip():
        enriched["latest_file"] = source
    if not str(enriched.get("latest_file_name", "")).strip():
        enriched["latest_file_name"] = source_name

    enriched["waveform_payload"] = build_waveform_payload(
        rows,
        source=source,
        width=max(320, int(width)),
        height=max(180, int(height)),
        max_points=max(32, int(max_points)),
        diagnosis_result=payload,
    )
    return enriched
