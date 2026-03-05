from __future__ import annotations

__all__ = [
    "SDKMinimalProbeConfig",
    "run_sdk_minimal_probe",
]


def __getattr__(name: str):
    if name in {"SDKMinimalProbeConfig", "run_sdk_minimal_probe"}:
        from .vb01_sdk_minimal import SDKMinimalProbeConfig, run_sdk_minimal_probe

        exports = {
            "SDKMinimalProbeConfig": SDKMinimalProbeConfig,
            "run_sdk_minimal_probe": run_sdk_minimal_probe,
        }
        return exports[name]
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
