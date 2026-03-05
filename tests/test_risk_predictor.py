import unittest

from elevator_monitor.risk_predictor import OnlineRiskPredictor


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


class TestRiskPredictor(unittest.TestCase):
    def test_disabled_predictor(self):
        predictor = OnlineRiskPredictor(enabled=False)
        out = predictor.update(
            ts_ms=1000,
            row=_row(1),
            anomaly_result={"level": "normal", "score": 0.0, "reasons": [], "stale_repeat": 1},
            fault_result={"fault_type": "unknown", "fault_confidence": 0.0},
        )
        self.assertEqual(out["risk_level_now"], "disabled")

    def test_high_anomaly_raises_risk(self):
        predictor = OnlineRiskPredictor(
            baseline_min_records=80,
            trend_window_s=1800,
            smooth_alpha=0.2,
        )

        for i in range(120):
            predictor.update(
                ts_ms=i * 1000,
                row=_row(i, ax=0.02, ay=0.01, az=-0.99, t=25.0),
                anomaly_result={"level": "normal", "score": 0.1, "reasons": [], "stale_repeat": 1},
                fault_result={"fault_type": "unknown", "fault_confidence": 0.0},
            )

        out = predictor.update(
            ts_ms=121000,
            row=_row(121, ax=6.0, ay=0.2, az=-0.4, t=33.0),
            anomaly_result={"level": "anomaly", "score": 12.0, "reasons": ["z:A_mag:9.0"], "stale_repeat": 1},
            fault_result={"fault_type": "impact_shock", "fault_confidence": 0.8},
        )
        self.assertGreater(out["risk_score"], 0.45)
        self.assertIn(out["risk_level_now"], {"watch", "high", "critical"})

    def test_positive_trend_increases_24h_risk(self):
        predictor = OnlineRiskPredictor(
            baseline_min_records=40,
            trend_window_s=600,
            smooth_alpha=0.25,
        )

        for i in range(80):
            predictor.update(
                ts_ms=i * 1000,
                row=_row(i, ax=0.02, ay=0.01, az=-0.99, t=25.0),
                anomaly_result={"level": "normal", "score": 0.0, "reasons": [], "stale_repeat": 1},
                fault_result={"fault_type": "unknown", "fault_confidence": 0.0},
            )

        out = None
        for i in range(81, 101):
            out = predictor.update(
                ts_ms=i * 60 * 1000,
                row=_row(i, ax=0.5 + i * 0.02, ay=0.01, az=-0.8, t=25.0 + i * 0.03),
                anomaly_result={"level": "warning", "score": 4.0 + i * 0.05, "reasons": ["z:A_mag:4.5"], "stale_repeat": 1},
                fault_result={"fault_type": "vibration_increase", "fault_confidence": 0.6},
            )

        self.assertIsNotNone(out)
        self.assertGreater(out["risk_24h"], out["risk_score"])
        self.assertIn(out["risk_level_24h"], {"watch", "high", "critical"})

    def test_snapshot_and_load_state(self):
        predictor = OnlineRiskPredictor(baseline_min_records=40)
        for i in range(120):
            predictor.update(
                ts_ms=i * 1000,
                row=_row(i, ax=0.02, ay=0.01, az=-0.99, t=25.0),
                anomaly_result={"level": "normal", "score": 0.1, "reasons": [], "stale_repeat": 1},
                fault_result={"fault_type": "unknown", "fault_confidence": 0.0},
            )

        state = predictor.snapshot_state(max_items=200)
        predictor2 = OnlineRiskPredictor(baseline_min_records=40)
        predictor2.load_state(state)
        out = predictor2.update(
            ts_ms=200000,
            row=_row(200, ax=5.0, ay=0.2, az=-0.5, t=33.0),
            anomaly_result={"level": "warning", "score": 7.0, "reasons": ["z:A_mag:5.0"], "stale_repeat": 1},
            fault_result={"fault_type": "vibration_increase", "fault_confidence": 0.7},
        )
        self.assertGreaterEqual(out["risk_score"], 0.0)

    def test_model_probability_boosts_risk(self):
        predictor = OnlineRiskPredictor(
            baseline_min_records=40,
            smooth_alpha=0.2,
            model_weight=0.5,
        )
        for i in range(80):
            predictor.update(
                ts_ms=i * 1000,
                row=_row(i, ax=0.02, ay=0.01, az=-0.99, t=25.0),
                anomaly_result={"level": "normal", "score": 0.0, "reasons": [], "stale_repeat": 1},
                fault_result={"fault_type": "unknown", "fault_confidence": 0.0},
            )

        out = predictor.update(
            ts_ms=90000,
            row=_row(90, ax=0.03, ay=0.02, az=-0.98, t=25.0),
            anomaly_result={"level": "normal", "score": 0.2, "reasons": [], "stale_repeat": 1},
            fault_result={"fault_type": "unknown", "fault_confidence": 0.0},
            model_probability=0.9,
        )
        self.assertGreater(out["risk_score"], 0.1)
        self.assertIn("risk_model=", out["risk_reasons"])


if __name__ == "__main__":
    unittest.main()
