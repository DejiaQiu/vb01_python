from __future__ import annotations

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


def attach_latest_waveforms(
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
