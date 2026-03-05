"""Elevator Monitor package.

Core implementation for production realtime monitoring.
"""

from .common import CORE_FIELDS, FEATURE_FIELDS, REG_MAP

__all__ = [
    "CORE_FIELDS",
    "FEATURE_FIELDS",
    "REG_MAP",
    "RealtimeVibrationReader",
    "VIBRATION_FIELDS",
    "build_vibration_frame",
]


def __getattr__(name: str):
    if name in {"RealtimeVibrationReader", "VIBRATION_FIELDS", "build_vibration_frame"}:
        from .realtime_vibration import RealtimeVibrationReader, VIBRATION_FIELDS, build_vibration_frame

        exports = {
            "RealtimeVibrationReader": RealtimeVibrationReader,
            "VIBRATION_FIELDS": VIBRATION_FIELDS,
            "build_vibration_frame": build_vibration_frame,
        }
        return exports[name]
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
