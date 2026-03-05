from __future__ import annotations

import os
import time


APP_TITLE = "Elevator Monitor"
DEFAULT_DEVICE_NAME = "elevator-monitor"


def _read_env(key: str) -> str | None:
    return os.getenv(key)


def env_str(key: str, default: str) -> str:
    value = _read_env(key)
    return value if value not in (None, "") else default


def env_int(key: str, default: int) -> int:
    value = _read_env(key)
    if value is None:
        return default
    try:
        return int(value, 0)
    except ValueError:
        return default


def env_float(key: str, default: float) -> float:
    value = _read_env(key)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def env_bool(key: str, default: bool) -> bool:
    value = _read_env(key)
    if value is None:
        return default

    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def ts_csv_path(prefix: str) -> str:
    return f"data/{prefix}_{time.strftime('%Y%m%d_%H%M%S')}.csv"
