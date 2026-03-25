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


def _payload_anchor_dirs(payload: dict[str, Any]) -> list[Path]:
    anchors: list[Path] = []

    def _push(path_text: str) -> None:
        text = str(path_text or "").strip()
        if not text:
            return
        path = Path(text).expanduser()
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        else:
            path = path.resolve()
        candidates = [path]
        if path.is_file():
            candidates.extend(path.parents)
        else:
            candidates.extend(path.parents)
        for candidate in candidates:
            if candidate not in anchors:
                anchors.append(candidate)

    _push(str(payload.get("latest_json", "")))
    _push(str(payload.get("latest_root", "")))
    _push(str(payload.get("input_dir", "")))
    for candidate in (Path.cwd(), Path.cwd() / "data", Path.cwd() / "data" / "captures"):
        resolved = candidate.resolve()
        if resolved not in anchors:
            anchors.append(resolved)
    return anchors


def _path_candidates(path_text: str, *, anchors: list[Path]) -> list[Path]:
    raw = str(path_text or "").strip()
    if not raw:
        return []
    original = Path(raw).expanduser()
    candidates: list[Path] = []

    def _push(path: Path) -> None:
        candidate = path.expanduser()
        if not candidate.is_absolute():
            candidate = (Path.cwd() / candidate).resolve()
        else:
            candidate = candidate.resolve()
        if candidate not in candidates:
            candidates.append(candidate)

    if original.is_absolute():
        _push(original)
    else:
        _push(original)
        for anchor in anchors:
            _push(anchor / original)

    parts = [part for part in original.parts if part and part != original.anchor]
    if parts:
        for idx, part in enumerate(parts):
            if part == "data":
                suffix = Path(*parts[idx:])
                _push(suffix)
                for anchor in anchors:
                    _push(anchor / suffix)
        for tail_len in range(1, min(6, len(parts)) + 1):
            suffix = Path(*parts[-tail_len:])
            for anchor in anchors:
                _push(anchor / suffix)
    return candidates


def _resolve_payload_path(path_text: str, payload: dict[str, Any], *, label: str) -> Path:
    raw = str(path_text or "").strip()
    if not raw:
        raise ValueError(f"{label} missing in latest status payload")

    anchors = _payload_anchor_dirs(payload)
    for candidate in _path_candidates(raw, anchors=anchors):
        if candidate.exists():
            return candidate

    name = Path(raw).name
    search_roots = [path for path in anchors if path.exists() and path.is_dir()]
    for root in search_roots:
        try:
            match = next(root.rglob(name), None) if name else None
        except Exception:
            match = None
        if match and match.exists():
            return match.resolve()

    display_name = name or raw
    raise FileNotFoundError(f"{label} not found in current environment: {display_name}")


def _load_rows_from_context(context: dict[str, Any], payload: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    stored_path = str(context.get("stored_path", "")).strip() or str(context.get("local_path", "")).strip()
    if not stored_path:
        raise ValueError("waveform context path missing in latest status payload")
    path = _resolve_payload_path(stored_path, payload, label="waveform context")
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
        resolved = _resolve_payload_path(latest_file, payload, label="waveform source")
        rows, source = load_waveform_rows([], "", str(resolved))
        return rows, source, latest_file_name or Path(source).name

    context = payload.get("context", {}) if isinstance(payload.get("context"), dict) else {}
    rows, source = _load_rows_from_context(context, payload)
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
