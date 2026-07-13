import os
import unittest
from unittest.mock import patch

from core import config


class RuntimeSecurityConfigTests(unittest.TestCase):
    def test_development_allows_local_shell_backend(self):
        with patch.dict(os.environ, {"NUKE_ENV": "development"}), \
             patch.object(config, "SHELL_EXEC_BACKEND", "local"):
            config.validate_runtime_security()

    def test_production_requires_container_shell_backend(self):
        with patch.dict(os.environ, {"NUKE_ENV": "production"}), \
             patch.object(config, "SHELL_EXEC_BACKEND", "local"):
            with self.assertRaisesRegex(RuntimeError, "requires.*container"):
                config.validate_runtime_security()

    def test_production_accepts_container_shell_backend(self):
        with patch.dict(os.environ, {"NUKE_ENV": "production"}), \
             patch.object(config, "SHELL_EXEC_BACKEND", "container"):
            config.validate_runtime_security()
