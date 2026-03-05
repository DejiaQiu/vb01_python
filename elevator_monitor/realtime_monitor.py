from __future__ import annotations

"""Compatibility entrypoint for realtime monitor.

Core implementation lives in elevator_monitor.monitor.runtime.
"""

from .monitor.alerting import ALERT_FIELDS, RISK_LEVEL_RANK
from .monitor.args import build_arg_parser
from .monitor.constants import DATA_FIELDS, RAIL_WEAR_FIELDS
from .monitor.pipeline import OnlineAnomalyDetector
from .monitor.runtime import RealtimeMonitor, main

__all__ = [
    "ALERT_FIELDS",
    "DATA_FIELDS",
    "RAIL_WEAR_FIELDS",
    "OnlineAnomalyDetector",
    "RISK_LEVEL_RANK",
    "RealtimeMonitor",
    "build_arg_parser",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
