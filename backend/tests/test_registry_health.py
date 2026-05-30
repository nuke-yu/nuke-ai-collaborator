"""
tests/test_registry_health.py — DFT-049 插件 import 错误不再静默吞

registry._load_file 原先把 import 失败只写一行日志即吞掉，全部失败时 get()
的 next(iter(_registry.values())) 还会抛 StopIteration。现在：失败被记入
failures() 可经 API 暴露健康状态，get() 在空注册表时抛明确 RuntimeError。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from executors import registry


class TestPluginHealth(unittest.TestCase):

    def setUp(self):
        self._saved_registry = dict(registry._registry)
        self._saved_dir = registry.PLUGIN_DIR

    def tearDown(self):
        registry._registry.clear()
        registry._registry.update(self._saved_registry)
        registry.PLUGIN_DIR = self._saved_dir

    def test_failed_import_is_recorded_not_swallowed(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "bad_plugin.py"
            bad.write_text("import this_module_definitely_does_not_exist_xyz\n")
            registry.PLUGIN_DIR = Path(d)
            registry.discover()
            failures = registry.failures()
        self.assertIn("bad_plugin.py", failures)
        self.assertNotIn("bad_plugin", registry._registry)

    def test_successful_discover_clears_stale_failures(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "bad_plugin.py"
            bad.write_text("raise ImportError('boom')\n")
            registry.PLUGIN_DIR = Path(d)
            registry.discover()
            self.assertIn("bad_plugin.py", registry.failures())
            # now point at an empty dir and rediscover → stale failure cleared
            bad.unlink()
            registry.discover()
        self.assertEqual(registry.failures(), {})

    def test_get_on_empty_registry_raises_runtimeerror(self):
        registry._registry.clear()
        with self.assertRaises(RuntimeError):
            registry.get("anything")


if __name__ == "__main__":
    unittest.main()
