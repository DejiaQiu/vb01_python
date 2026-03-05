from __future__ import annotations

from ..common import REG_MAP

DATA_FIELDS = ["elevator_id", "ts_ms", "ts", *REG_MAP.keys()]
RAIL_WEAR_FIELDS = [
    "trip_id",
    "timestamp",
    "rms_0_20hz",
    "smoothed_rms",
    "baseline_ratio",
    "alarm_flag",
    "fault_status",
    "days_since_baseline",
]

__all__ = ["DATA_FIELDS", "RAIL_WEAR_FIELDS"]
