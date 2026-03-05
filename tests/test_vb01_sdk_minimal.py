from __future__ import annotations

import unittest

from elevator_monitor.integrations.vb01_sdk_minimal import SDKMinimalProbeConfig, run_sdk_minimal_probe


class _FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, float(seconds))


class _FakeDeviceNoData:
    def __init__(self, *_args, **_kwargs):
        self.loop_started = False
        self.closed = False

    def openDevice(self):
        return True

    def startLoopRead(self, **_kwargs):
        self.loop_started = True

    def stopLoopRead(self):
        self.loop_started = False

    def closeDevice(self):
        self.closed = True

    def get(self, _key):
        return None


class _FakeDeviceWithData(_FakeDeviceNoData):
    def __init__(self, *_args, **_kwargs):
        super().__init__()
        self.counter = 0

    def get(self, key):
        if key in {"52", "53", "54"}:
            self.counter += 1
            if self.counter >= 2:
                return 0.123
        return None


class TestVB01SDKMinimal(unittest.TestCase):
    def test_probe_reports_startup_timeout_when_no_data(self):
        clock = _FakeClock()
        cfg = SDKMinimalProbeConfig(startup_timeout_s=0.2, duration_s=0.2)
        result = run_sdk_minimal_probe(
            cfg,
            device_factory=_FakeDeviceNoData,
            monotonic=clock.monotonic,
            sleeper=clock.sleep,
        )
        self.assertFalse(result["ok"])
        self.assertEqual("startup_timeout", result["status"])
        self.assertEqual(0, result["sample_count"])

    def test_probe_collects_samples_when_data_available(self):
        clock = _FakeClock()
        cfg = SDKMinimalProbeConfig(startup_timeout_s=0.5, duration_s=0.5, sample_hz=5.0)
        result = run_sdk_minimal_probe(
            cfg,
            device_factory=_FakeDeviceWithData,
            monotonic=clock.monotonic,
            sleeper=clock.sleep,
        )
        self.assertTrue(result["ok"])
        self.assertEqual("ok", result["status"])
        self.assertGreater(result["sample_count"], 0)


if __name__ == "__main__":
    unittest.main()
