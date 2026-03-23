import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from elevator_monitor.batch_diagnosis import load_latest_status, run_batch_diagnosis


def _write_csv(path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=["ts_ms", "Ax", "Ay", "Az", "Gx", "Gy", "Gz", "is_new_frame"])
        writer.writeheader()
        ts0 = 1_000_000
        for i in range(12):
            writer.writerow(
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


def _compact_fault(fault_type: str, score: float, *, triggered: bool = False, screening: str = "") -> dict:
    if score >= 60.0:
        level = "warning"
    elif score >= 45.0:
        level = "watch"
    else:
        level = "normal"
    payload = {
        "fault_type": fault_type,
        "score": score,
        "level": level,
        "triggered": triggered,
        "quality_factor": 1.0,
        "reasons": [f"score={score:.1f}"],
        "feature_snapshot": {"n": 12},
    }
    if screening:
        payload["screening"] = screening
    return payload


def _result(status: str, *, top_fault: dict, top_candidate: dict | None = None, watch_faults: list[dict] | None = None) -> dict:
    top_candidate = top_candidate or {}
    watch_faults = watch_faults or []
    candidate_faults = [top_candidate] if top_candidate else []
    return {
        "summary": {
            "n_raw": 463,
            "n_effective": 463,
            "fs_hz": 40.0,
            "used_new_only": True,
            "new_ratio": 1.0,
            "sampling_ok": True,
            "sampling_ok_40hz": True,
            "sampling_condition": "sampling_ok",
            "axis_mapping_mode": "default",
            "axis_mapping_signature": "vertical=Az|lateral_x=Ax|lateral_y=Ay",
        },
        "baseline": {"mode": "disabled", "count": 0, "stats": 0},
        "screening": {
            "status": status,
            "quality_ok": True,
            "high_confidence_min_score": 60.0,
            "watch_min_score": 45.0,
            "candidate_count": len(candidate_faults),
            "watch_count": len(watch_faults),
            "sampling_condition": "sampling_ok",
        },
        "rope_primary": {
            "fault_type": top_fault.get("fault_type", ""),
            "score": top_fault.get("score", 0.0),
            "level": top_fault.get("level", "normal"),
            "triggered": bool(top_fault.get("triggered", False)),
            "rope_rule_score": top_fault.get("score", 0.0),
            "rope_branch": "",
            "rope_spectral_snapshot": {},
        },
        "top_fault": top_fault,
        "top_candidate": top_candidate,
        "candidate_faults": candidate_faults,
        "watch_faults": watch_faults,
        "results": [top_fault],
    }


class TestBatchDiagnosis(unittest.TestCase):
    def test_normal_status_does_not_promote_top_fault_to_preferred_issue(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            csv_path = root / "vibration_30s_20260303_101500.csv"
            _write_csv(csv_path)

            fake_result = _result(
                "normal",
                top_fault=_compact_fault("rubber_hardening", 22.0),
            )

            with patch("elevator_monitor.batch_diagnosis.run_all_rows", return_value=fake_result):
                payload = run_batch_diagnosis(
                    input_dir=str(root),
                    max_files=1,
                    write_outputs=False,
                )

        self.assertEqual(payload["status"], "normal")
        self.assertEqual(payload["preferred_issue"], {})
        self.assertEqual(payload["latest_result"]["top_fault"]["fault_type"], "rubber_hardening")
        self.assertEqual(payload["latest_result"]["rope_primary"]["fault_type"], "rubber_hardening")

    def test_run_batch_diagnosis_writes_latest_status_and_history(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            paths = [
                root / "vibration_30s_20260303_101500.csv",
                root / "vibration_30s_20260303_103800.csv",
                root / "vibration_30s_20260303_104600.csv",
            ]
            for path in paths:
                _write_csv(path)

            latest_json = root / "out" / "latest_status.json"
            history_jsonl = root / "out" / "history.jsonl"

            fake_results = [
                _result(
                    "normal",
                    top_fault=_compact_fault("car_imbalance", 22.0),
                ),
                _result(
                    "watch_only",
                    top_fault=_compact_fault("rope_looseness", 52.0),
                    watch_faults=[_compact_fault("rope_looseness", 52.0, screening="watch")],
                ),
                _result(
                    "candidate_faults",
                    top_fault=_compact_fault("rope_looseness", 72.0, triggered=True),
                    top_candidate=_compact_fault("rope_looseness", 72.0, triggered=True, screening="high_confidence"),
                ),
            ]

            with patch("elevator_monitor.batch_diagnosis.run_all_rows", side_effect=fake_results):
                payload = run_batch_diagnosis(
                    input_dir=str(root),
                    max_files=3,
                    latest_json=str(latest_json),
                    history_jsonl=str(history_jsonl),
                    write_outputs=True,
                )

            self.assertEqual(payload["status"], "candidate_faults")
            self.assertEqual(payload["primary_issue"]["fault_type"], "rope_looseness")
            self.assertEqual(payload["preferred_issue"]["fault_type"], "rope_looseness")
            self.assertIn("risk", payload)
            self.assertIn("report_markdown_draft", payload)
            self.assertIn("一句话结论", payload["report_markdown_draft"])
            self.assertIn("连续窗口确认", payload["report_markdown_draft"])
            self.assertIn("## 波形图", payload["report_markdown_draft"])
            self.assertIn("waveform_payload", payload)
            self.assertIn("markdown_echarts", payload["waveform_payload"])
            self.assertIn("insight_markdown", payload["waveform_payload"])
            self.assertIn("low_frequency_spectrum", payload["waveform_payload"]["echarts"])
            self.assertEqual(payload["latest_result"]["rope_primary"]["fault_type"], "rope_looseness")
            self.assertTrue(latest_json.exists())
            self.assertTrue(history_jsonl.exists())

            latest_payload = json.loads(latest_json.read_text(encoding="utf-8"))
            self.assertEqual(latest_payload["latest_file_name"], "vibration_30s_20260303_104600.csv")
            self.assertEqual(len(latest_payload["history"]), 3)
            self.assertEqual(latest_payload["primary_issue"]["fault_type"], "rope_looseness")
            self.assertIn("report_markdown_draft", latest_payload)
            self.assertIn("当前最值得关注的问题", latest_payload["report_markdown_draft"])
            self.assertIn("连续窗口确认", latest_payload["report_markdown_draft"])
            self.assertIn("## 波形图", latest_payload["report_markdown_draft"])
            self.assertIn("waveform_payload", latest_payload)
            self.assertIn("markdown_echarts", latest_payload["waveform_payload"])
            self.assertIn("insight_markdown", latest_payload["waveform_payload"])
            self.assertIn("low_frequency_spectrum", latest_payload["waveform_payload"]["echarts"])
            self.assertIn("waveform_payload", latest_payload["latest_result"])

            history_lines = history_jsonl.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(history_lines), 1)
            self.assertIn("rope_looseness", history_lines[0])

    def test_load_latest_status_reads_saved_payload(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "latest_status.json"
            payload = {
                "workflow_type": "scheduled_batch_diagnosis_v1",
                "status": "watch_only",
                "preferred_issue": {"fault_type": "rubber_hardening", "score": 55.0},
            }
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            loaded = load_latest_status(str(path))

        self.assertEqual(loaded["status"], "watch_only")
        self.assertEqual(loaded["preferred_issue"]["fault_type"], "rubber_hardening")

    def test_primary_issue_is_preferred_over_legacy_watch_fields(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            csv_path = root / "vibration_30s_20260303_101500.csv"
            _write_csv(csv_path)

            fake_result = _result(
                "watch_only",
                top_fault=_compact_fault("rope_tension_abnormal", 53.0),
                watch_faults=[_compact_fault("rope_tension_abnormal", 53.0, screening="watch")],
            )
            fake_result["primary_issue"] = _compact_fault("rubber_hardening", 57.0, screening="watch")

            with patch("elevator_monitor.batch_diagnosis.run_all_rows", return_value=fake_result):
                payload = run_batch_diagnosis(
                    input_dir=str(root),
                    max_files=1,
                    write_outputs=False,
                )

        self.assertEqual(payload["status"], "watch_only")
        self.assertEqual(payload["preferred_issue"]["fault_type"], "rubber_hardening")


if __name__ == "__main__":
    unittest.main()
