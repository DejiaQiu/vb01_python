from __future__ import annotations

import csv
import datetime as dt
import glob
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from .window_features import WINDOW_FEATURE_FIELDS, extract_window_features


@dataclass(frozen=True)
class FaultEvent:
    elevator_id: str
    start_ms: int
    end_ms: int
    fault_type: str


@dataclass
class WindowSample:
    elevator_id: str
    window_start_ms: int
    window_end_ms: int
    source_file: str
    target_fault_type: str
    target_fault_24h: int
    target_next_fault_type: str
    features: dict[str, float]

    def to_row(self) -> dict[str, Any]:
        row = {
            "elevator_id": self.elevator_id,
            "window_start_ms": self.window_start_ms,
            "window_end_ms": self.window_end_ms,
            "source_file": self.source_file,
            "target_fault_type": self.target_fault_type,
            "target_fault_24h": self.target_fault_24h,
            "target_next_fault_type": self.target_next_fault_type,
        }
        row.update(self.features)
        return row


_TS_FIELDS = (
    "ts_ms",
    "timestamp_ms",
    "time_ms",
    "ts",
    "timestamp",
    "time",
)

_START_FIELDS = (
    "start_ts_ms",
    "start_ms",
    "fault_time_ms",
    "fault_ts_ms",
    "start_ts",
    "start_time",
    "fault_time",
    "fault_ts",
    "ts_ms",
    "ts",
)

_END_FIELDS = (
    "end_ts_ms",
    "end_ms",
    "end_ts",
    "end_time",
)

_ELEVATOR_FIELDS = (
    "elevator_id",
    "lift_id",
    "device_id",
)

_FAULT_FIELDS = (
    "fault_type",
    "label",
    "fault_label",
)

_TRUTHY = {"1", "true", "yes", "y", "ok", "confirmed"}


class EventTimeline:
    def __init__(self, events: list[FaultEvent]):
        self.events = sorted(events, key=lambda x: (x.start_ms, x.end_ms))
        self.starts = [e.start_ms for e in self.events]

    def overlapping_fault_type(self, start_ms: int, end_ms: int, default: str = "normal") -> str:
        if not self.events:
            return default

        idx = bisect_right(self.starts, end_ms)
        best_fault = default
        best_overlap = 0
        for event in self.events[:idx]:
            if event.end_ms <= start_ms:
                continue
            overlap = min(end_ms, event.end_ms) - max(start_ms, event.start_ms)
            if overlap > best_overlap:
                best_overlap = overlap
                best_fault = event.fault_type
        return best_fault

    def next_fault(self, end_ms: int, horizon_ms: int) -> Optional[FaultEvent]:
        if not self.events:
            return None

        idx = bisect_right(self.starts, end_ms)
        if idx >= len(self.events):
            return None
        event = self.events[idx]
        if event.start_ms <= end_ms + horizon_ms:
            return event
        return None


def discover_data_files(patterns: Iterable[str]) -> list[Path]:
    files: list[Path] = []
    for pattern in patterns:
        hits = [Path(p) for p in sorted(glob.glob(pattern))]
        files.extend(hits)
    uniq: list[Path] = []
    seen: set[str] = set()
    for path in files:
        key = str(path.resolve())
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        uniq.append(path)
    return uniq


def parse_ts_ms(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if value <= 0:
            return None
        return int(value)

    s = str(value).strip()
    if not s:
        return None

    try:
        v = float(s)
        if v > 0:
            return int(v)
    except ValueError:
        pass

    text = s.replace("T", " ").replace("Z", "").strip()
    formats = (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S.%f",
    )
    for fmt in formats:
        try:
            parsed = dt.datetime.strptime(text, fmt)
            return int(parsed.timestamp() * 1000)
        except ValueError:
            continue

    return None


def _pick_non_empty(row: dict[str, Any], fields: Iterable[str]) -> Optional[str]:
    for key in fields:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _normalize_elevator_id(row: dict[str, Any], fallback: str) -> str:
    value = _pick_non_empty(row, _ELEVATOR_FIELDS)
    if value:
        return value
    return fallback


def _parse_confirmed(row: dict[str, Any]) -> bool:
    raw = row.get("confirmed")
    if raw is None:
        return True
    return str(raw).strip().lower() in _TRUTHY


def load_fault_events(label_csv: Optional[str], default_event_duration_s: float = 300.0) -> dict[str, EventTimeline]:
    if not label_csv:
        return {}

    path = Path(label_csv)
    if not path.exists():
        raise FileNotFoundError(f"label csv not found: {path}")

    default_duration_ms = int(max(1.0, default_event_duration_s) * 1000)
    grouped: dict[str, list[FaultEvent]] = {}

    with path.open("r", encoding="utf-8", newline="") as fp:
        for row in csv.DictReader(fp):
            if not _parse_confirmed(row):
                continue

            fault_type = _pick_non_empty(row, _FAULT_FIELDS)
            if not fault_type:
                continue

            start_raw = _pick_non_empty(row, _START_FIELDS)
            if start_raw is None:
                continue
            start_ms = parse_ts_ms(start_raw)
            if start_ms is None:
                continue

            end_raw = _pick_non_empty(row, _END_FIELDS)
            end_ms = parse_ts_ms(end_raw) if end_raw is not None else None
            if end_ms is None:
                end_ms = start_ms + default_duration_ms
            end_ms = max(end_ms, start_ms)

            elevator_id = _normalize_elevator_id(row, "elevator-unknown")
            grouped.setdefault(elevator_id, []).append(
                FaultEvent(
                    elevator_id=elevator_id,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    fault_type=fault_type.strip(),
                )
            )

    return {eid: EventTimeline(events) for eid, events in grouped.items()}


def _infer_fallback_elevator_id(path: Path) -> str:
    stem = path.stem
    if not stem:
        return "elevator-unknown"
    if "_" in stem:
        head = stem.split("_", 1)[0].strip()
        if head:
            return head
    return stem


def _load_rows_from_csv(path: Path, default_elevator_id: Optional[str]) -> dict[str, list[tuple[int, dict[str, Any]]]]:
    grouped: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    fallback = default_elevator_id or _infer_fallback_elevator_id(path)
    source_file = str(path.expanduser().resolve())

    with path.open("r", encoding="utf-8", newline="") as fp:
        for row in csv.DictReader(fp):
            ts_val = _pick_non_empty(row, _TS_FIELDS)
            ts_ms = parse_ts_ms(ts_val) if ts_val is not None else None
            if ts_ms is None:
                continue
            enriched_row = dict(row)
            enriched_row["_source_file"] = source_file
            elevator_id = _normalize_elevator_id(row, fallback)
            grouped.setdefault(elevator_id, []).append((ts_ms, enriched_row))

    return grouped


def _iter_windows(
    records: list[tuple[int, dict[str, Any]]],
    window_ms: int,
    step_ms: int,
    min_samples: int,
) -> Iterable[tuple[int, int, list[dict[str, Any]]]]:
    if not records:
        return

    n = len(records)
    start_i = 0
    end_i = 0
    win_start = records[0][0]
    last_ts = records[-1][0]

    while win_start + window_ms <= last_ts:
        win_end = win_start + window_ms

        while start_i < n and records[start_i][0] < win_start:
            start_i += 1
        if end_i < start_i:
            end_i = start_i
        while end_i < n and records[end_i][0] < win_end:
            end_i += 1

        count = end_i - start_i
        if count >= min_samples:
            yield win_start, win_end, [row for _, row in records[start_i:end_i]]

        win_start += step_ms


def build_window_samples(
    data_files: list[Path],
    event_timelines: dict[str, EventTimeline],
    window_s: float,
    step_s: float,
    horizon_s: float,
    min_samples: int,
    default_elevator_id: Optional[str] = None,
) -> list[WindowSample]:
    window_ms = int(max(1.0, window_s) * 1000)
    step_ms = int(max(1.0, step_s) * 1000)
    horizon_ms = int(max(1.0, horizon_s) * 1000)

    grouped_records: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for path in data_files:
        rows_by_elevator = _load_rows_from_csv(path, default_elevator_id=default_elevator_id)
        for elevator_id, rows in rows_by_elevator.items():
            grouped_records.setdefault(elevator_id, []).extend(rows)

    for rows in grouped_records.values():
        rows.sort(key=lambda x: x[0])

    samples: list[WindowSample] = []
    for elevator_id, rows in grouped_records.items():
        timeline = event_timelines.get(elevator_id)
        if timeline is None:
            timeline = event_timelines.get("elevator-unknown")

        for win_start, win_end, win_rows in _iter_windows(rows, window_ms, step_ms, min_samples=min_samples):
            features = extract_window_features(win_rows)
            source_file = ""
            for row in win_rows:
                raw_source = row.get("_source_file")
                if raw_source:
                    source_file = str(raw_source)
                    break

            fault_type = "normal"
            target_fault_24h = 0
            target_next_fault_type = "none"
            if timeline is not None:
                fault_type = timeline.overlapping_fault_type(win_start, win_end, default="normal")
                next_event = timeline.next_fault(win_end, horizon_ms=horizon_ms)
                if next_event is not None:
                    target_fault_24h = 1
                    target_next_fault_type = next_event.fault_type

            samples.append(
                WindowSample(
                    elevator_id=elevator_id,
                    window_start_ms=win_start,
                    window_end_ms=win_end,
                    source_file=source_file,
                    target_fault_type=fault_type,
                    target_fault_24h=target_fault_24h,
                    target_next_fault_type=target_next_fault_type,
                    features=features,
                )
            )

    samples.sort(key=lambda x: (x.elevator_id, x.window_start_ms))
    return samples


def dataset_fieldnames() -> list[str]:
    return [
        "elevator_id",
        "window_start_ms",
        "window_end_ms",
        "source_file",
        "target_fault_type",
        "target_fault_24h",
        "target_next_fault_type",
        *WINDOW_FEATURE_FIELDS,
    ]
