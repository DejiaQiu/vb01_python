import os
import unittest
from unittest.mock import patch

from elevator_monitor.runtime_config import env_bool, env_float, env_int, env_str


class TestRuntimeConfig(unittest.TestCase):
    def test_env_str_returns_default_when_missing_or_empty(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(env_str("MONITOR_PORT", "/dev/ttyUSB0"), "/dev/ttyUSB0")

        with patch.dict(os.environ, {"MONITOR_PORT": ""}, clear=True):
            self.assertEqual(env_str("MONITOR_PORT", "/dev/ttyUSB0"), "/dev/ttyUSB0")

        with patch.dict(os.environ, {"MONITOR_PORT": "/dev/ttyS1"}, clear=True):
            self.assertEqual(env_str("MONITOR_PORT", "/dev/ttyUSB0"), "/dev/ttyS1")

    def test_env_int_supports_hex_and_default_fallback(self):
        with patch.dict(os.environ, {"MONITOR_ADDR": "0x50"}, clear=True):
            self.assertEqual(env_int("MONITOR_ADDR", 0x40), 0x50)

        with patch.dict(os.environ, {"MONITOR_ADDR": "bad"}, clear=True):
            self.assertEqual(env_int("MONITOR_ADDR", 0x40), 0x40)

    def test_env_float_uses_primary(self):
        with patch.dict(os.environ, {"MONITOR_SAMPLE_HZ": "80"}, clear=True):
            self.assertEqual(env_float("MONITOR_SAMPLE_HZ", 100.0), 80.0)

    def test_env_bool_parse(self):
        with patch.dict(os.environ, {"MONITOR_FAULT_TYPE_ENABLED": "true"}, clear=True):
            self.assertTrue(env_bool("MONITOR_FAULT_TYPE_ENABLED", False))

        with patch.dict(os.environ, {"MONITOR_FAULT_TYPE_ENABLED": "0"}, clear=True):
            self.assertFalse(env_bool("MONITOR_FAULT_TYPE_ENABLED", True))


if __name__ == "__main__":
    unittest.main()
