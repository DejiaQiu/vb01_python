from __future__ import annotations

import csv
import hashlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from .window_features import WINDOW_FEATURE_FIELDS


@dataclass
class LabeledSample:
    row: dict[str, Any]
    label: str


def load_dataset_rows(path: str) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"dataset not found: {p}")

    with p.open("r", encoding="utf-8", newline="") as fp:
        return list(csv.DictReader(fp))


def row_to_feature_vector(row: dict[str, Any], feature_names: list[str]) -> list[float]:
    vec: list[float] = []
    for name in feature_names:
        raw = row.get(name, 0.0)
        try:
            vec.append(float(raw))
        except (TypeError, ValueError):
            vec.append(0.0)
    return vec


def sample_group_key(row: dict[str, Any], group_column: str = "source_file") -> str:
    if group_column:
        raw = row.get(group_column)
        if raw is not None:
            text = str(raw).strip()
            if text:
                return f"{group_column}:{text}"
    return f"fallback:{row.get('elevator_id','')}:{row.get('window_start_ms','')}"


def split_train_val(
    samples: list[LabeledSample],
    val_ratio: float,
    seed: int,
    key_func: Optional[Callable[[dict[str, Any]], str]] = None,
    group_column: str = "source_file",
) -> tuple[list[LabeledSample], list[LabeledSample]]:
    if key_func is None:
        key_func = lambda row: sample_group_key(row, group_column=group_column)

    grouped: dict[str, list[LabeledSample]] = {}
    for sample in samples:
        grouped.setdefault(key_func(sample.row), []).append(sample)

    train: list[LabeledSample] = []
    val: list[LabeledSample] = []
    threshold = max(0.0, min(1.0, val_ratio))
    for group_key, group_samples in grouped.items():
        key = f"{seed}:{group_key}"
        h = hashlib.sha1(key.encode("utf-8")).hexdigest()
        ratio = int(h[:8], 16) / 0xFFFFFFFF
        if ratio < threshold:
            val.extend(group_samples)
        else:
            train.extend(group_samples)

    if not train and val:
        moved_key = sorted(grouped.keys())[0]
        moved = grouped[moved_key]
        val = [sample for sample in val if sample not in moved]
        train.extend(moved)
    if not val and train:
        moved_key = sorted(grouped.keys())[0]
        moved = grouped[moved_key]
        train = [sample for sample in train if sample not in moved]
        val.extend(moved)
    return train, val


def class_distribution(samples: list[LabeledSample]) -> dict[str, int]:
    return dict(Counter(s.label for s in samples))


def filter_by_min_samples(samples: list[LabeledSample], min_class_samples: int) -> list[LabeledSample]:
    min_required = max(1, min_class_samples)
    counter = Counter(s.label for s in samples)
    allowed = {label for label, count in counter.items() if count >= min_required}
    return [sample for sample in samples if sample.label in allowed]


def cap_class_ratio(
    samples: list[LabeledSample],
    majority_label: str,
    max_ratio: float,
    seed: int,
) -> list[LabeledSample]:
    if max_ratio <= 0:
        return samples

    grouped: dict[str, list[LabeledSample]] = {}
    for sample in samples:
        grouped.setdefault(sample.label, []).append(sample)

    majority = grouped.get(majority_label)
    if not majority:
        return samples

    minority_counts = [len(v) for k, v in grouped.items() if k != majority_label]
    if not minority_counts:
        return samples

    cap = int(max(1.0, max_ratio * max(minority_counts)))
    if len(majority) <= cap:
        return samples

    # Deterministic downsampling using hashed key.
    decorated: list[tuple[int, LabeledSample]] = []
    for sample in majority:
        key = f"{seed}:{sample.row.get('elevator_id','')}:{sample.row.get('window_start_ms','')}"
        h = hashlib.sha1(key.encode("utf-8")).hexdigest()
        decorated.append((int(h[:8], 16), sample))
    decorated.sort(key=lambda x: x[0])

    keep_majority = [sample for _, sample in decorated[:cap]]
    kept: list[LabeledSample] = []
    for label, rows in grouped.items():
        if label == majority_label:
            kept.extend(keep_majority)
        else:
            kept.extend(rows)
    return kept


def default_feature_names(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return list(WINDOW_FEATURE_FIELDS)

    present = set(rows[0].keys())
    names = [name for name in WINDOW_FEATURE_FIELDS if name in present]
    if not names:
        raise ValueError("dataset does not include expected feature columns")
    return names
