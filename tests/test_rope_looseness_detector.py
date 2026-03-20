import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from report.fault_algorithms._base import build_feature_pack
from report.fault_algorithms.detect_rope_looseness import detect
from report.fault_algorithms.rope_looseness_timeline import run_timeline


def _baseline(scale_factor: float = 1.0) -> dict:
    scaled = {
        "a_rms_ac": (0.0050, 0.0009),
        "a_p2p": (0.0280, 0.0060),
        "g_std": (0.0220, 0.0045),
        "zc_rate_hz": (1.80, 0.42),
        "lateral_ratio": (1.05, 0.09),
        "ag_corr": (0.14, 0.035),
        "gx_ax_corr": (0.14, 0.035),
        "gy_ay_corr": (0.14, 0.035),
        "peak_rate_hz": (0.30, 0.08),
        "a_crest": (1.60, 0.25),
        "a_kurt": (0.70, 0.40),
    }
    stats = {}
    for key, (median, scale) in scaled.items():
        multiplier = scale_factor if key in {"a_rms_ac", "a_p2p", "g_std"} else 1.0
        stats[key] = {
            "median": median * multiplier,
            "scale": scale * multiplier,
        }
    return {"stats": stats, "count": 48}


def _feature_overrides(**kwargs):
    base = {
        "n": 24,
        "fs_hz": 5.0,
        "duration_s": 24.0,
        "used_new_only": True,
        "a_mean": 1.0,
        "g_mean": 0.90,
        "a_rms_ac": 0.002,
        "a_p2p": 0.015,
        "a_std": 0.003,
        "a_crest": 1.3,
        "a_kurt": 0.5,
        "g_std": 0.010,
        "peak_rate_hz": 0.1,
        "zc_rate_hz": 4.0,
        "lateral_ratio": 1.0,
        "ag_corr": 0.10,
        "gx_ax_corr": 0.10,
        "gy_ay_corr": 0.10,
        "jerk_rms": 0.002,
        "rope_disable_model": True,
    }
    base.update(kwargs)
    return base


class TestRopeLoosenessDetector(unittest.TestCase):
    def test_feature_pack_extracts_frequency_features(self):
        rows = []
        ts0 = 1_000_000
        for i in range(120):
            angle = (2.0 * math.pi * i) / 20.0
            rows.append(
                {
                    "ts_ms": ts0 + i * 50,
                    "Ax": 0.03 * math.sin(angle),
                    "Ay": 0.02 * math.cos(angle),
                    "Az": -0.98 + 0.08 * math.sin(angle),
                    "Gx": 0.1,
                    "Gy": 0.2,
                    "Gz": 0.3,
                    "is_new_frame": 1,
                }
            )

        features = build_feature_pack(rows)

        self.assertGreater(features["lat_dom_freq_hz"], 0.4)
        self.assertGreater(features["lat_peak_ratio"], 0.01)
        self.assertGreater(features["z_dom_freq_hz"], 0.4)
        self.assertGreater(features["z_peak_ratio"], 0.01)

    def test_scale_invariant_when_using_baseline(self):
        base_result = detect(
            _feature_overrides(
                baseline=_baseline(1.0),
                a_mean=1.0,
                g_mean=0.90,
                a_rms_ac=0.016,
                a_p2p=0.092,
                g_std=0.095,
                zc_rate_hz=0.35,
                peak_rate_hz=0.12,
                lateral_ratio=1.88,
                ag_corr=0.46,
                gx_ax_corr=0.52,
                gy_ay_corr=0.45,
                a_crest=1.75,
                a_kurt=1.10,
            )
        )
        scaled_result = detect(
            _feature_overrides(
                baseline=_baseline(2.0),
                a_mean=2.0,
                g_mean=1.80,
                a_rms_ac=0.032,
                a_p2p=0.184,
                g_std=0.190,
                zc_rate_hz=0.35,
                peak_rate_hz=0.12,
                lateral_ratio=1.88,
                ag_corr=0.46,
                gx_ax_corr=0.52,
                gy_ay_corr=0.45,
                a_crest=1.75,
                a_kurt=1.10,
            )
        )

        self.assertTrue(base_result["triggered"], msg=base_result)
        self.assertTrue(scaled_result["triggered"], msg=scaled_result)
        self.assertLess(abs(base_result["score"] - scaled_result["score"]), 4.0)
        self.assertIn("baseline_mode=robust_baseline", base_result["reasons"])
        self.assertIn("baseline_mode=robust_baseline", scaled_result["reasons"])

    def test_baseline_normal_like_sample_stays_suppressed(self):
        result = detect(
            _feature_overrides(
                baseline=_baseline(1.0),
                a_mean=1.0,
                g_mean=0.90,
                a_rms_ac=0.0052,
                a_p2p=0.029,
                g_std=0.0225,
                zc_rate_hz=1.75,
                peak_rate_hz=0.30,
                lateral_ratio=1.08,
                ag_corr=0.15,
                gx_ax_corr=0.16,
                gy_ay_corr=0.15,
                a_crest=1.62,
                a_kurt=0.72,
            )
        )

        self.assertLess(result["score"], 35.0, msg=result)
        self.assertFalse(result["triggered"], msg=result)

    def test_fallback_mode_triggers_for_clear_low_frequency_sway(self):
        result = detect(
            _feature_overrides(
                a_mean=1.0,
                g_mean=0.90,
                a_rms_ac=0.015,
                a_p2p=0.090,
                g_std=0.085,
                zc_rate_hz=0.40,
                peak_rate_hz=0.15,
                lateral_ratio=1.90,
                ag_corr=0.48,
                gx_ax_corr=0.53,
                gy_ay_corr=0.44,
                a_crest=1.70,
                a_kurt=1.00,
            )
        )

        self.assertGreaterEqual(result["score"], 60.0)
        self.assertTrue(result["triggered"])
        self.assertIn("baseline_mode=self_normalized_fallback", result["reasons"])

    def test_structural_baseline_mode_can_rescue_low_amplitude_slack(self):
        result = detect(
            _feature_overrides(
                baseline=_baseline(1.0),
                a_mean=1.0,
                g_mean=1.05,
                a_rms_ac=0.0064,
                a_p2p=0.025,
                g_std=0.078,
                zc_rate_hz=0.18,
                peak_rate_hz=0.05,
                lateral_ratio=2.75,
                ag_corr=0.18,
                gx_ax_corr=0.82,
                gy_ay_corr=0.31,
                a_crest=1.02,
                a_kurt=0.10,
            )
        )

        self.assertGreaterEqual(result["score"], 60.0, msg=result)
        self.assertTrue(result["triggered"], msg=result)
        self.assertIn("confirm=candidate_hits_pass", result["reasons"])

    def test_spiky_impact_signature_does_not_trigger_rope_looseness(self):
        result = detect(
            _feature_overrides(
                a_mean=1.0,
                g_mean=0.90,
                a_rms_ac=0.018,
                a_p2p=0.140,
                g_std=0.080,
                zc_rate_hz=8.0,
                peak_rate_hz=5.5,
                lateral_ratio=0.85,
                ag_corr=0.12,
                gx_ax_corr=0.10,
                gy_ay_corr=0.09,
                a_crest=4.8,
                a_kurt=8.0,
            )
        )

        self.assertLess(result["score"], 60.0)
        self.assertFalse(result["triggered"])

    def test_vertical_rubber_like_signature_stays_below_candidate(self):
        result = detect(
            _feature_overrides(
                baseline=_baseline(1.0),
                a_mean=1.0,
                g_mean=0.90,
                a_rms_ac=0.013,
                a_p2p=0.085,
                g_std=0.060,
                zc_rate_hz=5.8,
                peak_rate_hz=0.35,
                lateral_ratio=0.82,
                ag_corr=0.12,
                gx_ax_corr=0.11,
                gy_ay_corr=0.10,
                energy_z_over_xy=1.95,
                az_p2p=0.26,
                az_cv=1.10,
                az_jerk_rms=0.95,
                lat_dom_freq_hz=4.20,
                lat_peak_ratio=0.18,
                lat_low_band_ratio=0.16,
                z_dom_freq_hz=2.10,
                z_peak_ratio=0.58,
                z_low_band_ratio=0.66,
                a_crest=1.95,
                a_kurt=1.40,
            )
        )

        self.assertLess(result["score"], 60.0, msg=result)
        self.assertFalse(result["triggered"], msg=result)

    def test_timeline_requires_consecutive_windows(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            times = ["101700", "101730", "101800", "101830", "101900"]
            for hhmmss in times:
                path = Path(tmp_dir) / f"vibration_30s_20260303_{hhmmss}.csv"
                path.write_text("ts_ms,Ax,Ay,Az\n", encoding="utf-8")

            scores = [22.0, 72.0, 74.0, 28.0, 71.0]
            fake_results = [
                {
                    "score": score,
                    "level": "warning" if score >= 60.0 else "normal",
                    "reasons": ["mode=test"],
                    "quality_factor": 1.0,
                    "feature_snapshot": {"n": 24},
                }
                for score in scores
            ]

            with (
                patch("report.fault_algorithms.rope_looseness_timeline.load_rows", return_value=[]),
                patch("report.fault_algorithms.rope_looseness_timeline.build_feature_pack", return_value={"n": 24}),
                patch("report.fault_algorithms.rope_looseness_timeline.detect", side_effect=fake_results),
            ):
                payload = run_timeline(
                    input_dir=Path(tmp_dir),
                    start_hhmm="1017",
                    end_hhmm="1019",
                    min_score=60.0,
                    confirm_windows=2,
                )

        self.assertEqual(payload["raw_trigger_count"], 3)
        self.assertEqual(payload["confirmed_trigger_count"], 2)
        confirmed = [row["confirmed_triggered"] for row in payload["rows"]]
        self.assertEqual(confirmed, [False, True, True, False, False])

    def test_timeline_skips_low_quality_gap_during_confirmation(self):
        rows = [
            {"raw_triggered": True, "skip_confirmation": False, "confirmed_triggered": False},
            {"raw_triggered": False, "skip_confirmation": True, "confirmed_triggered": False},
            {"raw_triggered": True, "skip_confirmation": False, "confirmed_triggered": False},
        ]

        from report.fault_algorithms.rope_looseness_timeline import _apply_consecutive_confirmation

        _apply_consecutive_confirmation(rows, confirm_windows=2)

        self.assertEqual([row["confirmed_triggered"] for row in rows], [True, False, True])


if __name__ == "__main__":
    unittest.main()
