from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .runtime_config import APP_TITLE


def check_health(health_path: Path, max_age_s: float) -> tuple[bool, str]:
    if not health_path.exists():
        return False, f"missing health file: {health_path}"

    try:
        payload = json.loads(health_path.read_text(encoding="utf-8"))
    except Exception as ex:
        return False, f"invalid health json: {ex}"

    status = str(payload.get("status", "")).lower()
    updated_at_ms = payload.get("updated_at_ms")

    if status not in {"running", "reconnecting", "connecting"}:
        return False, f"bad status: {status or 'unknown'}"

    try:
        updated_at_ms = int(updated_at_ms)
    except Exception:
        return False, "missing updated_at_ms"

    age_s = (time.time() * 1000 - updated_at_ms) / 1000.0
    if age_s > max_age_s:
        return False, f"health stale: age_s={age_s:.2f}"

    return True, f"ok status={status} age_s={age_s:.2f}"


def main() -> int:
    parser = argparse.ArgumentParser(description=f"{APP_TITLE} realtime monitor healthcheck")
    parser.add_argument("--health-path", type=Path, required=True, help="health json path")
    parser.add_argument("--max-age-s", type=float, default=30.0, help="max allowed age")
    args = parser.parse_args()

    ok, msg = check_health(args.health_path, max(1.0, args.max_age_s))
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
