import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from report.fault_algorithms import run_all as run_all_module


def _rows(count: int = 463, *, step_ms: int = 25):
    ts0 = 1_000_000
    rows = []
    for i in range(count):
        rows.append(
            {
                "ts_ms": ts0 + i * step_ms,
                "Ax": 0.01,
                "Ay": 0.02,
                "Az": -0.98,
                "Gx": 0.1,
                "Gy": 0.2,
                "Gz": 0.3,
                "t": 25.0,
                "is_new_frame": 1,
            }
        )
    return rows


def _write_csv(path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=["ts_ms", "Ax", "Ay", "Az", "Gx", "Gy", "Gz", "is_new_frame"])
        writer.writeheader()
        ts0 = 1_000_000
        for i in range(463):
            writer.writerow(
                {
                    "ts_ms": ts0 + i * 25,
                    "Ax": 0.01,
                    "Ay": 0.02,
                    "Az": -0.98,
                    "Gx": 0.1,
                    "Gy": 0.2,
                    "Gz": 0.3,
                    "is_new_frame": 1,
                }
            )


def _baseline_payload() -> dict:
    signature = "vertical=Az|lateral_x=Ax|lateral_y=Ay"
    return {
        "count": 8,
        "axis_mapping_signature": signature,
        "axis_mapping_mode": "default",
        "stats": {
            "a_rms_ac": {"median": 0.0202, "scale": 0.0030},
            "a_p2p": {"median": 0.3059, "scale": 0.0876},
            "g_std": {"median": 0.1960, "scale": 0.0235},
            "a_peak_std": {"median": 0.0216, "scale": 0.0051},
            "a_pca_primary_ratio": {"median": 0.4802, "scale": 0.1015},
            "a_band_log_ratio_0_5_over_5_20": {"median": 0.1964, "scale": 0.0400},
            "lateral_ratio": {"median": 1.1311, "scale": 0.0184},
            "lat_dom_freq_hz": {"median": 2.1000, "scale": 0.1483},
            "lat_low_band_ratio": {"median": 0.2985, "scale": 0.0041},
            "z_peak_ratio": {"median": 0.1283, "scale": 0.0004},
        },
    }


def _system_features(**overrides: float | str | bool) -> dict:
    signature = "vertical=Az|lateral_x=Ax|lateral_y=Ay"
    payload: dict[str, float | str | bool] = {
        "sampling_ok": True,
        "sampling_ok_40hz": True,
        "sampling_condition": "sampling_ok",
        "axis_mapping_signature": signature,
        "a_mean": 1.0,
        "g_mean": 0.3,
        "a_rms_ac": 0.0205,
        "a_p2p": 0.3120,
        "g_std": 0.1980,
        "a_peak_std": 0.0220,
        "a_pca_primary_ratio": 0.4820,
        "a_band_log_ratio_0_5_over_5_20": 0.2020,
        "lateral_ratio": 1.1200,
        "lat_dom_freq_hz": 2.0000,
        "lat_low_band_ratio": 0.3040,
        "lat_peak_ratio": 0.10,
        "z_peak_ratio": 0.1300,
    }
    payload.update(overrides)
    return payload


class TestFaultAlgorithmsRunAll(unittest.TestCase):
    def test_normal_sample_returns_no_issue(self):
        with patch.object(
            run_all_module,
            "_system_abnormality",
            return_value={
                "status": "normal",
                "score": 22.0,
                "shared_abnormal_score": 22.0,
                "baseline_mode": "robust_baseline",
                "baseline_weight": 0.85,
                "baseline_features": 10,
                "baseline_match": True,
                "run_state_score": 62.0,
                "gate_mode": "running",
                "shared_hits": 1,
                "shared_strong_hits": 0,
                "shared_feature_total": 10,
                "top_deviations": [],
                "sampling_ok": True,
                "sampling_ok_40hz": True,
                "sampling_condition": "sampling_ok",
            },
        ):
            payload = run_all_module.run_all_rows(_rows(), source="inline_rows")

        self.assertEqual(payload["screening"]["status"], "normal")
        self.assertEqual(payload["top_fault"], {})
        self.assertEqual(payload["candidate_faults"], [])
        self.assertEqual(payload["watch_faults"], [])
        self.assertEqual(payload["primary_issue"], {})
        self.assertEqual(payload["rope_primary"], {})
        self.assertEqual(payload["rubber_primary"], {})

    def test_system_watch_returns_unknown_watch_issue(self):
        with patch.object(
            run_all_module,
            "_system_abnormality",
            return_value={
                "status": "watch_only",
                "score": 52.0,
                "shared_abnormal_score": 58.0,
                "baseline_mode": "robust_baseline",
                "baseline_weight": 0.85,
                "baseline_features": 10,
                "baseline_match": True,
                "run_state_score": 61.0,
                "gate_mode": "running",
                "shared_hits": 4,
                "shared_strong_hits": 2,
                "shared_feature_total": 10,
                "top_deviations": [],
                "sampling_ok": True,
                "sampling_ok_40hz": True,
                "sampling_condition": "sampling_ok",
            },
        ):
            payload = run_all_module.run_all_rows(_rows(), source="inline_rows")

        self.assertEqual(payload["screening"]["status"], "watch_only")
        self.assertEqual(payload["primary_issue"]["fault_type"], "unknown")
        self.assertEqual(payload["top_fault"]["fault_type"], "unknown")
        self.assertEqual(payload["watch_faults"][0]["fault_type"], "unknown")
        self.assertEqual(payload["candidate_faults"], [])

    def test_system_candidate_returns_unknown_candidate_issue(self):
        with patch.object(
            run_all_module,
            "_system_abnormality",
            return_value={
                "status": "candidate_faults",
                "score": 72.0,
                "shared_abnormal_score": 74.0,
                "baseline_mode": "robust_baseline",
                "baseline_weight": 0.85,
                "baseline_features": 10,
                "baseline_match": True,
                "run_state_score": 66.0,
                "gate_mode": "running",
                "shared_hits": 6,
                "shared_strong_hits": 3,
                "shared_feature_total": 10,
                "top_deviations": [],
                "sampling_ok": True,
                "sampling_ok_40hz": True,
                "sampling_condition": "sampling_ok",
            },
        ):
            payload = run_all_module.run_all_rows(_rows(), source="inline_rows")

        self.assertEqual(payload["screening"]["status"], "candidate_faults")
        self.assertEqual(payload["top_candidate"]["fault_type"], "unknown")
        self.assertEqual(payload["candidate_faults"][0]["screening"], "high_confidence")
        self.assertEqual(payload["watch_faults"], [])

    def test_low_quality_window_suppresses_abnormal(self):
        with patch.object(
            run_all_module,
            "_system_abnormality",
            return_value={
                "status": "candidate_faults",
                "score": 72.0,
                "shared_abnormal_score": 74.0,
                "baseline_mode": "robust_baseline",
                "baseline_weight": 0.85,
                "baseline_features": 10,
                "baseline_match": True,
                "run_state_score": 66.0,
                "gate_mode": "running",
                "shared_hits": 6,
                "shared_strong_hits": 3,
                "shared_feature_total": 10,
                "top_deviations": [],
                "sampling_ok": True,
                "sampling_ok_40hz": True,
                "sampling_condition": "sampling_ok",
            },
        ):
            payload = run_all_module.run_all_rows(_rows(count=100), source="inline_rows")

        self.assertEqual(payload["screening"]["status"], "low_quality")
        self.assertEqual(payload["top_fault"], {})
        self.assertEqual(payload["candidate_faults"], [])
        self.assertFalse(payload["summary"]["sampling_ok_40hz"])

    def test_system_abnormality_stays_normal_when_close_to_baseline(self):
        result = run_all_module._system_abnormality(_system_features(), _baseline_payload())

        self.assertEqual(result["status"], "normal")
        self.assertEqual(result["baseline_mode"], "robust_baseline")
        self.assertEqual(result["baseline_features"], len(run_all_module.SYSTEM_BASELINE_FEATURES))
        self.assertLess(result["shared_abnormal_score"], run_all_module.SYSTEM_GATE_CONFIG["shared_watch_min"])
        self.assertEqual(result["shared_hits"], 0)

    def test_system_abnormality_uses_bidirectional_baseline_deviation(self):
        result = run_all_module._system_abnormality(
            _system_features(
                a_rms_ac=0.0137,
                a_p2p=0.1800,
                g_std=0.1400,
                a_peak_std=0.0120,
                a_pca_primary_ratio=0.6200,
                a_band_log_ratio_0_5_over_5_20=0.2700,
                lateral_ratio=1.4500,
                lat_dom_freq_hz=0.4000,
                lat_low_band_ratio=0.4200,
                z_peak_ratio=0.0600,
            ),
            _baseline_payload(),
        )

        self.assertEqual(result["status"], "candidate_faults")
        self.assertEqual(result["baseline_mode"], "robust_baseline")
        self.assertGreaterEqual(result["shared_hits"], run_all_module.SYSTEM_GATE_CONFIG["candidate_hit_min"])
        self.assertGreaterEqual(result["shared_strong_hits"], run_all_module.SYSTEM_GATE_CONFIG["candidate_strong_min"])
        self.assertEqual(result["top_deviations"][0]["key"], "lat_dom_freq_hz")

    def test_baseline_keys_match_shared_anomaly_features(self):
        self.assertEqual(
            run_all_module.BASELINE_KEYS,
            tuple(str(spec["key"]) for spec in run_all_module.SYSTEM_BASELINE_FEATURES),
        )

    def test_build_baseline_from_segment_csv_dir(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            for idx in range(3):
                _write_csv(root / f"segment_{idx:02d}.csv")

            baseline = run_all_module._build_baseline_from_dir(root, "0000", "2359")

        self.assertIsNotNone(baseline)
        self.assertEqual(baseline["count"], 3)
        self.assertIn("a_peak_std", baseline["stats"])


if __name__ == "__main__":
    unittest.main()
