from __future__ import annotations

import argparse

import uvicorn
from fastapi import FastAPI

from .routers.diagnostics import router as diagnostics_router
from .routers.health import router as health_router
from .routers.ingest import router as ingest_router
from .routers.meta import router as meta_router
from .routers.workflows import router as workflows_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="Elevator Monitor API",
        version="1.0.0",
        description="Unified API for elevator vibration diagnostics and predictive maintenance packaging",
    )
    app.include_router(meta_router)
    app.include_router(health_router)
    app.include_router(ingest_router)
    app.include_router(diagnostics_router)
    app.include_router(workflows_router)
    return app


app = create_app()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Elevator Monitor FastAPI service")
    parser.add_argument("--host", default="0.0.0.0", help="bind host")
    parser.add_argument("--port", type=int, default=8085, help="bind port")
    parser.add_argument("--reload", action="store_true", help="enable autoreload")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    uvicorn.run("elevator_monitor.api.main:app", host=args.host, port=max(1, args.port), reload=args.reload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
