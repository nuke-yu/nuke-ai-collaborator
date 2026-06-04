import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import config

class TestConfig(unittest.TestCase):
    def test_config_values(self):
        self.assertEqual(config.WS_SEND_TIMEOUT, 10.0)
        self.assertEqual(config.SUPERVISOR_SEND_TIMEOUT, 5.0)
        self.assertEqual(config.IPC_MAX_FRAME_SIZE, 64 * 1024 * 1024)
        self.assertEqual(config.ASK_TIMEOUT_SECONDS, 300)
        self.assertEqual(config.SPAWN_MAX_DEPTH, 3)
        self.assertEqual(config.DOOM_LOOP_THRESHOLD, 3)
        self.assertEqual(config.SUMMARY_THRESHOLD, 15)
        self.assertEqual(config.AI_RETRY_MAX, 3)

if __name__ == "__main__":
    unittest.main()
