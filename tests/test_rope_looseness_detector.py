import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from report.fault_algorithms._base import axis_mapping_signature, build_feature_pack
from report.fault_algorithms.detect_rope_looseness import ROPE_RULE_CONFIG, detect
from report.fault_algorithms.rope_looseness_timeline import run_timeline


def _baseline(scale_factor: float = 1.0, *, mapping_signature: str | None = None) -> dict:
    scaled = {
        "a_rms_ac": (0.0050, 0.0009),
        "a_band_0_5_energy": (0.25, 0.08),
        "a_band_5_20_energy": (0.20, 0.06),
        "a_band_log_ratio_0_5_over_5_20": (0.82, 0.18),
        "a_zcr_hz": (11.5, 1.8),
        "a_peak_std": (0.0048, 0.0012),
        "a_pca_primary_ratio": (0.52, 0.06),
    }
    linear_keys = {"a_rms_ac", "a_peak_std"}
    quadratic_keys = {"a_band_0_5_energy", "a_band_5_20_energy"}
    stats = {}
    for key, (median, scale) in scaled.items():
        if key in linear_keys:
            multiplier = scale_factor
        elif key in quadratic_keys:
            multiplier = scale_factor * scale_factor
        else:
            multiplier = 1.0
        stats[key] = {
            "median": median * multiplier,
            "scale": scale * multiplier,
        }
    return {
        "stats": stats,
        "count": 48,
        "axis_mapping_signature": mapping_signature or axis_mapping_signature(None),
        "axis_mapping_mode": "default",
    }


def _feature_overrides(**kwargs):
    base = {
        "n": 463,
        "fs_hz": 40.0,
        "duration_s": 11.55,
        "sampling_ok": True,
        "sampling_ok_40hz": True,
        "sampling_condition": "sampling_ok",
        "axis_mapping_mode": "default",
        "axis_mapping_signature": axis_mapping_signature(None),
        "used_new_only": True,
        "a_mean": 1.0,
        "g_mean": 0.90,
        "a_rms_ac": 0.0052,
        "a_band_0_5_energy": 0.27,
        "a_band_5_20_energy": 0.21,
        "a_band_log_ratio_0_5_over_5_20": math.log1p(0.27 / 0.21),
        "a_p2p": 0.029,
        "a_std": 0.006,
        "a_crest": 1.62,
        "a_kurt": 0.72,
        "g_std": 0.0225,
        "peak_rate_hz": 0.20,
        "zc_rate_hz": 1.60,
        "a_zcr_hz": 11.8,
        "a_peak_std": 0.0047,
        "a_pca_primary_ratio": 0.53,
        "lateral_ratio": 1.08,
        "ag_corr": 0.15,
        "gx_ax_corr": 0.16,
        "gy_ay_corr": 0.15,
        "jerk_rms": 0.003,
        "energy_z_over_xy": 0.92,
        "az_p2p": 0.038,
        "az_cv": 0.82,
        "az_jerk_rms": 0.013,
        "lat_dom_freq_hz": 1.10,
        "lat_peak_ratio": 0.28,
        "lat_low_band_ratio": 0.42,
        "z_dom_freq_hz": 2.60,
        "z_peak_ratio": 0.20,
        "z_low_band_ratio": 0.26,
    }
    base.update(kwargs)
    return base


def _rows(*, rotated: bool = False) -> list[dict[str, float]]:
    rows = []
    ts0 = 1_000_000
    for i in range(480):
        t = i / 40.0
        lateral_x = 0.09 * math.sin(2.0 * math.pi * 0.8 * t)
        lateral_y = 0.05 * math.cos(2.0 * math.pi * 0.8 * t)
        vertical = -0.98 + 0.03 * math.sin(2.0 * math.pi * 2.6 * t)
        gx = 0.35 * math.sin(2.0 * math.pi * 0.8 * t)
        gy = 0.24 * math.cos(2.0 * math.pi * 0.8 * t)
        gz = 0.12 * math.sin(2.0 * math.pi * 2.6 * t)
        if rotated:
            rows.append(
                {
                    "ts_ms": ts0 + i * 25,
                    "Ax": vertical,
                    "Ay": lateral_x,
                    "Az": lateral_y,
                    "Gx": gz,
                    "Gy": gx,
                    "Gz": gy,
                    "is_new_frame": 1,
                }
            )
        else:
            rows.append(
                {
                    "ts_ms": ts0 + i * 25,
                    "Ax": lateral_x,
                    "Ay": lateral_y,
                    "Az": vertical,
                    "Gx": gx,
                    "Gy": gy,
                    "Gz": gz,
                    "is_new_frame": 1,
                }
            )
    return rows


class TestRopeLoosenessDetector(unittest.TestCase):
    def test_feature_pack_extracts_frequency_features_with_valid_sampling(self):
        features = build_feature_pack(_rows())

        self.assertTrue(features["sampling_ok"])
        self.assertTrue(features["sampling_ok_40hz"])
        self.assertGreater(features["a_band_0_5_energy"], 0.0)
        self.assertGreater(features["a_band_log_ratio_0_5_over_5_20"], 0.0)
        self.assertGreater(features["a_pca_primary_ratio"], 0.5)
        self.assertGreater(features["lat_dom_freq_hz"], 0.3)
        self.assertGreater(features["lat_low_band_ratio"], 0.10)

    def test_axis_mapping_recovers_rotated_installation(self):
        original = build_feature_pack(_rows())
        rotated = build_feature_pack(
            _rows(rotated=True),
            axis_mapping={"vertical": "Ax", "lateral_x": "Ay", "lateral_y": "Az"},
        )

        self.assertAlmostEqual(original["lateral_ratio"], rotated["lateral_ratio"], places=3)
        self.assertAlmostEqual(original["lat_dom_freq_hz"], rotated["lat_dom_freq_hz"], places=1)
        self.assertAlmostEqual(original["lat_low_band_ratio"], rotated["lat_low_band_ratio"], places=3)
        self.assertEqual(rotated["axis_mapping_mode"], "explicit")

    def test_feature_pack_marks_low_sampling_rate(self):
        rows = []
        ts0 = 1_000_000
        for i in range(24):
            rows.append(
                {
                    "ts_ms": ts0 + i * 1000,
                    "Ax": 0.01,
                    "Ay": 0.02,
                    "Az": -0.98,
                    "Gx": 0.1,
                    "Gy": 0.2,
                    "Gz": 0.3,
                    "is_new_frame": 1,
                }
            )
        features = build_feature_pack(rows)

        self.assertFalse(features["sampling_ok"])
        self.assertFalse(features["sampling_ok_40hz"])
        self.assertEqual(features["sampling_condition"], "low_sampling_rate")

    def test_scale_invariant_when_using_baseline(self):
        base_result = detect(
            _feature_overrides(
                baseline=_baseline(1.0),
                a_mean=1.0,
                g_mean=0.90,
                a_rms_ac=0.016,
                a_band_0_5_energy=1.40,
                a_band_5_20_energy=0.04,
                a_band_log_ratio_0_5_over_5_20=math.log1p(1.40 / 0.04),
                a_zcr_hz=4.6,
                a_peak_std=0.0015,
                a_pca_primary_ratio=0.84,
                a_p2p=0.092,
                g_std=0.095,
                peak_rate_hz=0.10,
                lateral_ratio=1.90,
                ag_corr=0.46,
                gx_ax_corr=0.52,
                gy_ay_corr=0.45,
                lat_dom_freq_hz=0.72,
                lat_low_band_ratio=0.76,
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
                a_band_0_5_energy=5.60,
                a_band_5_20_energy=0.16,
                a_band_log_ratio_0_5_over_5_20=math.log1p(5.60 / 0.16),
                a_zcr_hz=4.6,
                a_peak_std=0.0030,
                a_pca_primary_ratio=0.84,
                a_p2p=0.184,
                g_std=0.190,
                peak_rate_hz=0.10,
                lateral_ratio=1.90,
                ag_corr=0.46,
                gx_ax_corr=0.52,
                gy_ay_corr=0.45,
                lat_dom_freq_hz=0.72,
                lat_low_band_ratio=0.76,
                a_crest=1.75,
                a_kurt=1.10,
            )
        )

        self.assertGreaterEqual(base_result["score"], ROPE_RULE_CONFIG["watch_score"], msg=base_result)
        self.assertGreaterEqual(scaled_result["score"], ROPE_RULE_CONFIG["watch_score"], msg=scaled_result)
        self.assertLess(abs(base_result["score"] - scaled_result["score"]), 4.0)
        self.assertTrue(base_result["triggered"], msg=base_result)
        self.assertTrue(scaled_result["triggered"], msg=scaled_result)
        self.assertIn("baseline_match=true", base_result["reasons"])

    def test_baseline_normal_like_sample_stays_suppressed(self):
        result = detect(_feature_overrides(baseline=_baseline()))

        self.assertLess(result["score"], 35.0, msg=result)
        self.assertFalse(result["triggered"], msg=result)

    def test_baseline_mapping_mismatch_caps_to_watch(self):
        result = detect(
            _feature_overrides(
                baseline=_baseline(mapping_signature="vertical=Ax|lateral_x=Ay|lateral_y=Az"),
                a_rms_ac=0.016,
                a_band_0_5_energy=1.40,
                a_band_5_20_energy=0.04,
                a_band_log_ratio_0_5_over_5_20=math.log1p(1.40 / 0.04),
                a_zcr_hz=4.6,
                a_peak_std=0.0015,
                a_pca_primary_ratio=0.84,
                a_p2p=0.092,
                g_std=0.095,
                peak_rate_hz=0.10,
                lateral_ratio=1.90,
                ag_corr=0.46,
                gx_ax_corr=0.52,
                gy_ay_corr=0.45,
                lat_dom_freq_hz=0.72,
                lat_low_band_ratio=0.76,
            )
        )

        self.assertGreaterEqual(result["score"], ROPE_RULE_CONFIG["watch_score"], msg=result)
        self.assertLess(result["score"], ROPE_RULE_CONFIG["candidate_score"], msg=result)
        self.assertFalse(result["triggered"], msg=result)
        self.assertIn("baseline_match=false", result["reasons"])

    def test_low_sampling_rate_does_not_trigger_candidate(self):
        result = detect(
            _feature_overrides(
                baseline=_baseline(),
                n=120,
                fs_hz=2.0,
                duration_s=60.0,
                sampling_ok=False,
                sampling_ok_40hz=False,
                sampling_condition="low_sampling_rate",
                a_rms_ac=0.016,
                a_band_0_5_energy=1.40,
                a_band_5_20_energy=0.04,
                a_band_log_ratio_0_5_over_5_20=math.log1p(1.40 / 0.04),
                a_zcr_hz=4.6,
                a_peak_std=0.0015,
                a_pca_primary_ratio=0.84,
                a_p2p=0.092,
                g_std=0.095,
                lateral_ratio=1.90,
                lat_dom_freq_hz=0.72,
                lat_low_band_ratio=0.76,
            )
        )

        self.assertLess(result["score"], ROPE_RULE_CONFIG["watch_score"], msg=result)
        self.assertFalse(result["triggered"], msg=result)
        self.assertIn("sampling_condition=low_sampling_rate", result["reasons"])

    def test_spiky_impact_signature_does_not_trigger_rope_looseness(self):
        result = detect(
            _feature_overrides(
                baseline=_baseline(),
                a_rms_ac=0.018,
                a_band_0_5_energy=0.32,
                a_band_5_20_energy=0.95,
                a_band_log_ratio_0_5_over_5_20=math.log1p(0.32 / 0.95),
                a_zcr_hz=17.5,
                a_peak_std=0.024,
                a_pca_primary_ratio=0.38,
                a_p2p=0.140,
                g_std=0.080,
                peak_rate_hz=5.5,
                lateral_ratio=0.85,
                ag_corr=0.12,
                gx_ax_corr=0.10,
                gy_ay_corr=0.09,
                lat_dom_freq_hz=3.60,
                lat_low_band_ratio=0.18,
                a_crest=4.8,
                a_kurt=8.0,
            )
        )

        self.assertLess(result["score"], 60.0)
        self.assertFalse(result["triggered"])

    def test_vertical_rubber_like_signature_stays_below_candidate(self):
        result = detect(
            _feature_overrides(
                baseline=_baseline(),
                a_rms_ac=0.013,
                a_band_0_5_energy=0.40,
                a_band_5_20_energy=0.56,
                a_band_log_ratio_0_5_over_5_20=math.log1p(0.40 / 0.56),
                a_zcr_hz=11.0,
                a_peak_std=0.013,
                a_pca_primary_ratio=0.43,
                a_p2p=0.085,
                g_std=0.060,
                peak_rate_hz=0.35,
                lateral_ratio=0.82,
                ag_corr=0.12,
                gx_ax_corr=0.11,
                gy_ay_corr=0.10,
                energy_z_over_xy=1.95,
                az_p2p=0.26,
                az_cv=1.10,
                az_jerk_rms=0.095,
                lat_dom_freq_hz=3.20,
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
                    "feature_snapshot": {"n": 463},
                }
                for score in scores
            ]

            with (
                patch("report.fault_algorithms.rope_looseness_timeline.load_rows", return_value=[]),
                patch(
                    "report.fault_algorithms.rope_looseness_timeline.build_feature_pack",
                    return_value={
                        "n": 463,
                        "sampling_ok": True,
                        "sampling_ok_40hz": True,
                        "sampling_condition": "sampling_ok",
                    },
                ),
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
