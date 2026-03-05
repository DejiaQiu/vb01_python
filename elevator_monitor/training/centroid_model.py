from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


_EPS = 1e-9


@dataclass
class CentroidModel:
    task: str
    feature_names: list[str]
    classes: list[str]
    mean: list[float]
    scale: list[float]
    centroids: dict[str, list[float]]
    class_counts: dict[str, int]
    metrics: dict[str, Any]

    def _normalize(self, values: list[float]) -> list[float]:
        return [
            (value - self.mean[i]) / self.scale[i]
            for i, value in enumerate(values)
        ]

    def vector_from_row(self, row: dict[str, Any]) -> list[float]:
        vec: list[float] = []
        for name in self.feature_names:
            raw = row.get(name, 0.0)
            try:
                vec.append(float(raw))
            except (TypeError, ValueError):
                vec.append(0.0)
        return vec

    def predict_proba_vec(self, vec: list[float]) -> dict[str, float]:
        z = self._normalize(vec)
        dists: dict[str, float] = {}
        for cls in self.classes:
            centroid = self.centroids[cls]
            d2 = sum((a - b) ** 2 for a, b in zip(z, centroid))
            dists[cls] = math.sqrt(d2)

        # Softmax on negative distance: smaller distance -> larger probability.
        logits = {cls: -dist for cls, dist in dists.items()}
        max_logit = max(logits.values()) if logits else 0.0
        exp_scores = {cls: math.exp(logit - max_logit) for cls, logit in logits.items()}
        denom = sum(exp_scores.values()) or _EPS
        return {cls: score / denom for cls, score in exp_scores.items()}

    def predict_vec(self, vec: list[float]) -> tuple[str, float]:
        proba = self.predict_proba_vec(vec)
        if not proba:
            return "", 0.0
        best_class = max(proba, key=proba.get)
        return best_class, float(proba[best_class])

    def predict_row(self, row: dict[str, Any]) -> tuple[str, float]:
        vec = self.vector_from_row(row)
        return self.predict_vec(vec)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_type": "centroid_classifier_v1",
            "task": self.task,
            "feature_names": self.feature_names,
            "classes": self.classes,
            "mean": self.mean,
            "scale": self.scale,
            "centroids": self.centroids,
            "class_counts": self.class_counts,
            "metrics": self.metrics,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CentroidModel":
        return cls(
            task=str(payload.get("task", "unknown")),
            feature_names=[str(x) for x in payload.get("feature_names", [])],
            classes=[str(x) for x in payload.get("classes", [])],
            mean=[float(x) for x in payload.get("mean", [])],
            scale=[max(float(x), _EPS) for x in payload.get("scale", [])],
            centroids={str(k): [float(v) for v in values] for k, values in payload.get("centroids", {}).items()},
            class_counts={str(k): int(v) for k, v in payload.get("class_counts", {}).items()},
            metrics=dict(payload.get("metrics", {})),
        )

    def save(self, path: str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str) -> "CentroidModel":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(payload)


def _compute_normalization(vectors: list[list[float]]) -> tuple[list[float], list[float]]:
    dim = len(vectors[0]) if vectors else 0
    mean: list[float] = []
    scale: list[float] = []
    for i in range(dim):
        col = [vec[i] for vec in vectors]
        m = float(statistics.fmean(col))
        s = float(statistics.pstdev(col)) if len(col) > 1 else 0.0
        mean.append(m)
        scale.append(max(s, _EPS))
    return mean, scale


def _normalize_batch(vectors: list[list[float]], mean: list[float], scale: list[float]) -> list[list[float]]:
    out: list[list[float]] = []
    for vec in vectors:
        out.append([(value - mean[i]) / scale[i] for i, value in enumerate(vec)])
    return out


def _fit_centroids(vectors: list[list[float]], labels: list[str], classes: list[str]) -> tuple[dict[str, list[float]], dict[str, int]]:
    dim = len(vectors[0]) if vectors else 0
    sums: dict[str, list[float]] = {cls: [0.0] * dim for cls in classes}
    counts: dict[str, int] = {cls: 0 for cls in classes}

    for vec, label in zip(vectors, labels):
        counts[label] += 1
        acc = sums[label]
        for i, value in enumerate(vec):
            acc[i] += value

    centroids: dict[str, list[float]] = {}
    for cls in classes:
        cnt = max(1, counts[cls])
        centroids[cls] = [value / cnt for value in sums[cls]]

    return centroids, counts


def classification_metrics(y_true: list[str], y_pred: list[str], classes: list[str]) -> dict[str, Any]:
    total = len(y_true)
    correct = sum(1 for yt, yp in zip(y_true, y_pred) if yt == yp)
    accuracy = correct / total if total > 0 else 0.0

    per_class: dict[str, dict[str, float]] = {}
    supports: dict[str, int] = {cls: 0 for cls in classes}
    for label in y_true:
        supports[label] = supports.get(label, 0) + 1

    macro_f1_vals: list[float] = []
    weighted_f1_sum = 0.0
    for cls in classes:
        tp = sum(1 for yt, yp in zip(y_true, y_pred) if yt == cls and yp == cls)
        fp = sum(1 for yt, yp in zip(y_true, y_pred) if yt != cls and yp == cls)
        fn = sum(1 for yt, yp in zip(y_true, y_pred) if yt == cls and yp != cls)

        precision = tp / (tp + fp) if tp + fp > 0 else 0.0
        recall = tp / (tp + fn) if tp + fn > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall > 0 else 0.0

        per_class[cls] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": supports.get(cls, 0),
        }
        macro_f1_vals.append(f1)
        weighted_f1_sum += f1 * supports.get(cls, 0)

    macro_f1 = statistics.fmean(macro_f1_vals) if macro_f1_vals else 0.0
    weighted_f1 = weighted_f1_sum / total if total > 0 else 0.0

    return {
        "accuracy": accuracy,
        "macro_f1": macro_f1,
        "weighted_f1": weighted_f1,
        "support": total,
        "per_class": per_class,
    }


def fit_centroid_classifier(
    features: list[list[float]],
    labels: list[str],
    feature_names: list[str],
    task: str,
    eval_features: Optional[list[list[float]]] = None,
    eval_labels: Optional[list[str]] = None,
) -> CentroidModel:
    if len(features) != len(labels):
        raise ValueError("features and labels length mismatch")
    if not features:
        raise ValueError("empty features")

    classes = sorted({str(label) for label in labels})
    mean, scale = _compute_normalization(features)
    train_norm = _normalize_batch(features, mean, scale)
    centroids, class_counts = _fit_centroids(train_norm, labels, classes)

    model = CentroidModel(
        task=task,
        feature_names=feature_names,
        classes=classes,
        mean=mean,
        scale=scale,
        centroids=centroids,
        class_counts=class_counts,
        metrics={},
    )

    eval_x = eval_features if eval_features else features
    eval_y = eval_labels if eval_labels else labels
    preds: list[str] = []
    confs: list[float] = []
    for vec in eval_x:
        pred, conf = model.predict_vec(vec)
        preds.append(pred)
        confs.append(conf)

    metrics = classification_metrics(eval_y, preds, classes)
    metrics["avg_confidence"] = float(statistics.fmean(confs)) if confs else 0.0
    metrics["evaluated_on"] = "validation" if eval_features else "train"
    model.metrics = metrics
    return model
