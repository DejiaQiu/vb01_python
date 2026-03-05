import unittest
import math

from elevator_monitor.fault_types import FaultTypeEngine


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


class TestFaultTypeEngine(unittest.TestCase):
    def test_missing_data_classified(self):
        engine = FaultTypeEngine()
        result = engine.update(
            _row(1),
            {"level": "anomaly", "reasons": ["missing:0.86"], "stale_repeat": 1},
        )
        self.assertEqual(result["fault_type"], "sensor_missing")
        self.assertGreater(result["fault_confidence"], 0.5)

    def test_vibration_spike_classified(self):
        engine = FaultTypeEngine(
            baseline_min_records=80,
            vibration_warning_z=3.0,
            vibration_shock_z=6.0,
        )

        for i in range(120):
            engine.update(_row(i, ax=0.02, ay=0.01, az=-0.99), {"level": "normal", "reasons": [], "stale_repeat": 1})

        result = engine.update(
            _row(999, ax=8.0, ay=0.2, az=-0.5),
            {"level": "warning", "reasons": ["z:A_mag:10.0"], "stale_repeat": 1},
        )
        self.assertIn(result["fault_type"], {"impact_shock", "vibration_increase"})
        self.assertGreater(result["fault_confidence"], 0.0)

    def test_temperature_overheat_classified(self):
        engine = FaultTypeEngine(temp_overheat_c=40.0)
        result = engine.update(
            _row(10, t=46.0),
            {"level": "anomaly", "reasons": ["z:T:8.0"], "stale_repeat": 1},
        )
        self.assertEqual(result["fault_type"], "temperature_overheat")

    def test_rail_wear_classified(self):
        engine = FaultTypeEngine(
            baseline_min_records=60,
            vibration_warning_z=999.0,
            vibration_shock_z=1000.0,
            sample_hz=100.0,
        )

        # 正常段：小幅波动，建立导轨磨损算法基线。
        for i in range(200):
            ax = 0.01 * math.sin(i * 0.2)
            gx = 0.05 * math.sin(i * 0.1)
            engine.update(_row(i, ax=ax, ay=0.0, az=-1.0, gx=gx, gy=0.0, gz=0.0), {"level": "normal", "reasons": [], "stale_repeat": 1})

        result = {"fault_type": "unknown"}
        # 异常段：持续低频抖动升高，触发导轨磨损告警。
        for j in range(80):
            ts = 200 + j
            ax = 0.45 * math.sin(j * 0.18)
            gx = 1.10 * math.sin(j * 0.08)
            result = engine.update(
                _row(ts, ax=ax, ay=0.0, az=-1.0, gx=gx, gy=0.0, gz=0.0),
                {"level": "warning", "reasons": ["z:A_mag:4.0"], "stale_repeat": 1},
            )

        self.assertTrue(
            ("rail_wear_warning" in result["fault_candidates"]) or ("rail_wear_critical" in result["fault_candidates"]),
            msg=f"fault result without rail_wear candidate: {result}",
        )

    def test_min_level_gate(self):
        engine = FaultTypeEngine(min_level="anomaly")
        result = engine.update(
            _row(2),
            {"level": "warning", "reasons": ["missing:0.70"], "stale_repeat": 1},
        )
        self.assertEqual(result["fault_type"], "unknown")

    def test_snapshot_and_load_state(self):
        engine = FaultTypeEngine(baseline_min_records=40)
        for i in range(80):
            engine.update(_row(i, ax=0.03, ay=0.01, az=-0.98, t=25.0), {"level": "normal", "reasons": [], "stale_repeat": 1})

        state = engine.snapshot_state(max_items=100)
        engine2 = FaultTypeEngine(baseline_min_records=40)
        engine2.load_state(state)

        result = engine2.update(
            _row(999, ax=4.5, ay=0.4, az=-0.5, t=45.0),
            {"level": "anomaly", "reasons": ["z:A_mag:8.0"], "stale_repeat": 1},
        )
        self.assertIn(result["fault_type"], {"impact_shock", "vibration_increase", "temperature_overheat", "rail_wear_warning", "rail_wear_critical"})


if __name__ == "__main__":
    unittest.main()
