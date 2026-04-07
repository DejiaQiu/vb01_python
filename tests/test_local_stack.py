import tempfile
import unittest
from pathlib import Path

from elevator_monitor.local_stack import (
    LocalStackConfig,
    build_api_command,
    build_local_stack_paths,
    build_monitor_command,
)


class TestLocalStack(unittest.TestCase):
    def test_build_local_stack_paths_scopes_data_by_elevator(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_local_stack_paths(Path(tmp), "elevator/001")
            self.assertTrue(str(paths.data_dir).endswith("data/local_stack/elevator_001"))
            self.assertTrue(str(paths.monitor_log_path).endswith("logs/local_stack/elevator_001.monitor.log"))
            self.assertTrue(str(paths.profile_path).endswith("data/local_stack/elevator_001/profile.json"))

    def test_build_commands_enable_local_edge_sync(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = LocalStackConfig(
                elevator_id="elevator-001",
                serial_port="/dev/ttyUSB9",
                api_port=8099,
                project_root=Path(tmp),
            )
            paths = build_local_stack_paths(Path(tmp), config.elevator_id)
            api_command = build_api_command(config, python_executable="python3")
            monitor_command = build_monitor_command(config, python_executable="python3", paths=paths)

            self.assertEqual(api_command[:3], ["python3", "-m", "elevator_monitor.api.main"])
            self.assertIn("--edge-sync-enabled", monitor_command)
            self.assertIn("http://127.0.0.1:8099", monitor_command)
            self.assertIn(str(paths.health_path), monitor_command)
            self.assertIn(str(paths.output_alert_path), monitor_command)
            self.assertIn(str(paths.alert_context_dir), monitor_command)


if __name__ == "__main__":
    unittest.main()
