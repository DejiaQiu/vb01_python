from __future__ import annotations

"""Compatibility entrypoint for the FastAPI backend.

The structured backend now lives under elevator_monitor.api.
"""

from .api.main import app, build_arg_parser, create_app, main

__all__ = ["app", "build_arg_parser", "create_app", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
