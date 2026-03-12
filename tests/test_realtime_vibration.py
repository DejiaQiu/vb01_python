import unittest
import time

from elevator_monitor.monitor.args import build_arg_parser as build_monitor_arg_parser
from elevator_monitor.realtime_vibration import RealtimeVibrationReader, build_arg_parser, build_vibration_frame


class _FakeDevice:
    def __init__(self):
        self.data = {}
        self.last_ts_ms = None

    def get_snapshot(self, keys=None):
        if keys is None:
            return dict(self.data)
        return {k: self.data.get(k) for k in keys}

    def get_last_update_ts_ms(self):
        return self.last_ts_ms

    def wait_for_data(self, timeout_s=0.0):
        return self.last_ts_ms is not None


class TestRealtimeVibration(unittest.TestCase):
    def test_default_parsers_use_40hz(self):
        reader_args = build_arg_parser().parse_args([])
        monitor_args = build_monitor_arg_parser().parse_args([])

        self.assertEqual(reader_args.sample_hz, 40.0)
        self.assertEqual(reader_args.detect_hz, 40)
        self.assertEqual(reader_args.emit_hz, 40.0)
        self.assertEqual(monitor_args.sample_hz, 40.0)
        self.assertEqual(monitor_args.detect_hz, 40)

    def test_build_frame_without_data_returns_none(self):
        device = _FakeDevice()
        frame = build_vibration_frame(
            device=device,
            elevator_id="elevator-001",
            ts_ms=1_000,
            max_data_age_ms=500,
        )
        self.assertIsNone(frame)

    def test_build_frame_stale_returns_none(self):
        device = _FakeDevice()
        device.last_ts_ms = 1_000
        device.data = {"52": 0.1, "53": 0.2, "54": -0.3}

        frame = build_vibration_frame(
            device=device,
            elevator_id="elevator-001",
            ts_ms=2_000,
            max_data_age_ms=100,
        )
        self.assertIsNone(frame)

    def test_build_frame_success(self):
        device = _FakeDevice()
        device.last_ts_ms = 1_000
        device.data = {
            "52": 3.0,
            "53": 4.0,
            "54": 0.0,
            "55": 0.0,
            "56": 0.0,
            "57": 2.0,
            "64": 26.5,
        }

        frame = build_vibration_frame(
            device=device,
            elevator_id="elevator-001",
            ts_ms=1_200,
            max_data_age_ms=500,
        )
        self.assertIsNotNone(frame)
        self.assertEqual(frame["data_age_ms"], 200)
        self.assertEqual(frame["Ax"], 3.0)
        self.assertEqual(frame["t"], 26.5)
        self.assertAlmostEqual(frame["A_mag"], 5.0, places=6)
        self.assertAlmostEqual(frame["G_mag"], 2.0, places=6)

    def test_reader_require_new_frame(self):
        device = _FakeDevice()
        device.last_ts_ms = 1_000
        device.data = {
            "52": 0.1,
            "53": 0.2,
            "54": 0.3,
            "55": 0.4,
            "56": 0.5,
            "57": 0.6,
        }
        reader = RealtimeVibrationReader(
            elevator_id="elevator-001",
            device=device,
            owns_device=False,
            max_data_age_ms=1_000,
        )

        frame1 = reader.read_latest(ts_ms=1_100, require_new=True)
        frame2 = reader.read_latest(ts_ms=1_110, require_new=True)
        device.last_ts_ms = 1_200
        frame3 = reader.read_latest(ts_ms=1_220, require_new=True)

        self.assertIsNotNone(frame1)
        self.assertIsNone(frame2)
        self.assertIsNotNone(frame3)
        self.assertEqual(frame3["data_ts_ms"], 1_200)

    def test_iter_frames_timeout_when_no_new_data(self):
        device = _FakeDevice()
        reader = RealtimeVibrationReader(
            elevator_id="elevator-001",
            device=device,
            owns_device=False,
            max_data_age_ms=1_000,
        )

        with self.assertRaises(TimeoutError):
            next(reader.iter_frames(limit=1, poll_s=0.001, max_idle_s=0.1))

    def test_iter_frames_fixed_rate_marks_duplicate_frames(self):
        device = _FakeDevice()
        device.last_ts_ms = int(time.time() * 1000)
        device.data = {
            "52": 0.1,
            "53": 0.2,
            "54": 0.3,
            "55": 0.4,
            "56": 0.5,
            "57": 0.6,
            "64": 25.0,
        }
        reader = RealtimeVibrationReader(
            elevator_id="elevator-001",
            device=device,
            owns_device=False,
            max_data_age_ms=10_000,
        )

        frames = list(reader.iter_frames_fixed_rate(emit_hz=50.0, limit=4, max_idle_s=None))

        self.assertEqual(len(frames), 4)
        self.assertEqual(frames[0]["is_new_frame"], 1)
        self.assertEqual([f["is_new_frame"] for f in frames[1:]], [0, 0, 0])
        self.assertTrue(all(int(f["data_ts_ms"]) == int(device.last_ts_ms) for f in frames))


if __name__ == "__main__":
    unittest.main()
