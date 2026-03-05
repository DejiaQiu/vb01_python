from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .training.centroid_model import CentroidModel
from .training.window_features import extract_window_features


_NON_FAULT_LABELS = {"", "none", "normal", "unknown", "disabled"}


def is_non_fault_label(label: str) -> bool:
    return label.strip().lower() in _NON_FAULT_LABELS


@dataclass
class ModelPrediction:
    label: str
    confidence: float
    top_k: str
    probabilities: dict[str, float]


class OnlineWindowBuffer:
    def __init__(self, window_s: float = 10.0, min_samples: int = 20, max_samples: int = 5000):
        self.window_ms = int(max(1.0, window_s) * 1000)
        self.min_samples = max(1, min_samples)
        self._rows: deque[tuple[int, dict[str, Any]]] = deque(maxlen=max(100, max_samples))

    def update(self, ts_ms: int, row: dict[str, Any]) -> Optional[dict[str, float]]:
        self._rows.append((int(ts_ms), row))
        self._trim(ts_ms)
        if len(self._rows) < self.min_samples:
            return None
        return extract_window_features([record for _, record in self._rows])

    def _trim(self, now_ms: int) -> None:
        cutoff = now_ms - self.window_ms
        while self._rows and self._rows[0][0] < cutoff:
            self._rows.popleft()


class CentroidModelRunner:
    def __init__(self, model_path: str):
        path = Path(model_path).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"model not found: {path}")
        self.path = str(path)
        self.name = path.stem
        self.model = CentroidModel.load(self.path)

    def predict(self, features: dict[str, float], top_k: int = 3) -> Optional[ModelPrediction]:
        if not self.model.feature_names:
            return None

        vec: list[float] = []
        for name in self.model.feature_names:
            value = features.get(name, 0.0)
            try:
                vec.append(float(value))
            except (TypeError, ValueError):
                vec.append(0.0)

        proba = self.model.predict_proba_vec(vec)
        if not proba:
            return None

        ranked = sorted(proba.items(), key=lambda x: x[1], reverse=True)
        label, confidence = ranked[0]
        top = "|".join(f"{cls}:{score:.3f}" for cls, score in ranked[: max(1, top_k)])
        return ModelPrediction(
            label=str(label),
            confidence=float(confidence),
            top_k=top,
            probabilities={str(k): float(v) for k, v in proba.items()},
        )
