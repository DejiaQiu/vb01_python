from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def summarize_model(path: str) -> dict[str, Any]:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"model file not found: {p}")

    payload = json.loads(p.read_text(encoding="utf-8"))
    stat = p.stat()
    sha = sha256_file(p)

    metrics = payload.get("metrics", {})
    classes = payload.get("classes", [])
    feature_names = payload.get("feature_names", [])

    return {
        "id": f"{p.stem}-{sha[:12]}",
        "name": p.stem,
        "path": str(p),
        "sha256": sha,
        "size_bytes": stat.st_size,
        "mtime_ms": int(stat.st_mtime * 1000),
        "model_type": payload.get("model_type", "unknown"),
        "task": payload.get("task", "unknown"),
        "class_count": len(classes),
        "classes": [str(x) for x in classes],
        "feature_count": len(feature_names),
        "metrics": metrics,
    }


def build_manifest(
    model_paths: list[str],
    project: str,
    environment: str,
    created_by: str,
    note: str = "",
) -> dict[str, Any]:
    models = [summarize_model(path) for path in model_paths]
    now_ms = int(time.time() * 1000)
    return {
        "manifest_version": 1,
        "generated_at_ms": now_ms,
        "project": project,
        "environment": environment,
        "created_by": created_by,
        "note": note,
        "models": models,
    }
