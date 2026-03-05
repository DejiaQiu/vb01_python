import unittest

from elevator_monitor.realtime_monitor import OnlineAnomalyDetector


def _row(ts, ax=0.0, ay=0.0, az=-1.0, gx=0.1, gy=0.2, gz=0.3, t=25.0):
    return {
        "ts_ms": ts,
        "ts": f"t{ts}",
        "Ax": ax,
        "Ay": ay,
        "Az": az,
        "Gx": gx,
        "Gy": gy,
        "Gz": gz,
        "t": t,
        "vx": 0.0,
        "vy": 0.0,
        "vz": 0.0,
        "ax": 0.0,
        "ay": 0.0,
        "az": 0.0,
        "sx": 0.0,
        "sy": 0.0,
        "sz": 0.0,
        "fx": 0.0,
        "fy": 0.0,
        "fz": 0.0,
    }


class TestRealtimeMonitorDetector(unittest.TestCase):
    def test_baseline_warmup(self):
        detector = OnlineAnomalyDetector(
            baseline_size=200,
            baseline_min_records=30,
            baseline_refresh_every=10,
            stale_limit=300,
            warning_z=10.0,
            anomaly_z=20.0,
        )

        last = None
        for i in range(40):
            last = detector.update(_row(i, ax=0.0 + i * 1e-4))

        self.assertIsNotNone(last)
        self.assertTrue(detector.baseline_ready)
        self.assertIn(last["level"], {"normal", "warning"})
        self.assertNotEqual(last["level"], "anomaly")

    def test_missing_turns_anomaly(self):
        detector = OnlineAnomalyDetector(baseline_min_records=20, baseline_refresh_every=10)

        for i in range(30):
            detector.update(_row(i, ax=0.0 + i * 1e-4))

        result = detector.update({"ts_ms": 999, "ts": "t999", "Ax": "", "Ay": "", "Az": "", "Gx": "", "Gy": "", "Gz": "", "t": ""})
        self.assertEqual(result["level"], "anomaly")
        self.assertTrue(any(r.startswith("missing:") for r in result["reasons"]))

    def test_repeated_values_trigger_stale(self):
        detector = OnlineAnomalyDetector(stale_limit=5, baseline_min_records=20, baseline_refresh_every=5)

        for i in range(30):
            detector.update(_row(i, ax=0.0 + i * 1e-4))

        result = None
        for i in range(20):
            result = detector.update(_row(2000 + i, ax=1.23, ay=4.56, az=7.89, gx=0.1, gy=0.2, gz=0.3, t=20.0))

        self.assertIsNotNone(result)
        self.assertEqual(result["level"], "anomaly")
        self.assertTrue(any(r.startswith("stale:") for r in result["reasons"]))


if __name__ == "__main__":
    unittest.main()
