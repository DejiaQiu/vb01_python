from __future__ import annotations

import csv
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Literal, Optional


def now_ts_ms() -> int:
    """Return unix timestamp in milliseconds."""
    return time.time_ns() // 1_000_000


def format_ts_ms(ts_ms: int) -> str:
    """Format unix ms timestamp as local time with milliseconds."""
    dt = datetime.fromtimestamp(ts_ms / 1000)
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


FileFormat = Literal["csv", "jsonl"]


@dataclass
class DataRecorder:
    path: str
    file_format: FileFormat = "csv"
    fieldnames: Optional[list[str]] = None
    flush: bool = True
    flush_every_n: Optional[int] = None

    _fp: Any = None
    _csv_writer: Optional[csv.DictWriter] = None
    _write_count: int = field(default=0, init=False, repr=False)

    def open(self) -> "DataRecorder":
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        if self.file_format == "csv":
            self._fp = open(self.path, "a", newline="", encoding="utf-8")
            self._init_csv_writer_if_needed()
        elif self.file_format == "jsonl":
            self._fp = open(self.path, "a", encoding="utf-8")
        else:
            raise ValueError(f"Unsupported file_format: {self.file_format}")
        return self

    def close(self) -> None:
        if self._fp is None:
            return
        try:
            self._fp.flush()
        finally:
            self._fp.close()
            self._fp = None
            self._csv_writer = None

    def __enter__(self) -> "DataRecorder":
        return self.open()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def write(self, record: dict[str, Any]) -> None:
        if self._fp is None:
            raise RuntimeError("Recorder is not open. Use DataRecorder(...).open() or a context manager.")

        if self.file_format == "csv":
            if self._csv_writer is None:
                self.fieldnames = self.fieldnames or list(record.keys())
                self._csv_writer = csv.DictWriter(self._fp, fieldnames=self.fieldnames, extrasaction="ignore")
                self._write_header_if_empty()
            self._csv_writer.writerow(record)
        elif self.file_format == "jsonl":
            self._fp.write(json.dumps(record, ensure_ascii=False) + "\n")
        else:
            raise ValueError(f"Unsupported file_format: {self.file_format}")

        self._write_count += 1
        if self.flush_every_n is not None:
            if self.flush_every_n <= 0:
                raise ValueError("flush_every_n must be a positive integer when provided.")
            if self._write_count % self.flush_every_n == 0:
                self._fp.flush()
        elif self.flush:
            self._fp.flush()

    def write_many(self, records: Iterable[dict[str, Any]]) -> None:
        for record in records:
            self.write(record)

    def _init_csv_writer_if_needed(self) -> None:
        if self.fieldnames is None:
            return
        self._csv_writer = csv.DictWriter(self._fp, fieldnames=self.fieldnames, extrasaction="ignore")
        self._write_header_if_empty()

    def _write_header_if_empty(self) -> None:
        try:
            is_empty = self._fp.tell() == 0
        except OSError:
            is_empty = False
        if is_empty and self._csv_writer is not None:
            self._csv_writer.writeheader()
