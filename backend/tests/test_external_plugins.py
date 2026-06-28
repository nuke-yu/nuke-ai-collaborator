import os
import tempfile
import shutil
import pytest
from pathlib import Path

from executors import registry
from executors.base import BotExecutor

MOCK_PLUGIN_CONTENT = """
from executors.base import BotExecutor

class MockExternalExecutor(BotExecutor):
    executor_id = "mock_external_executor"
    display_name = "Mock External Executor"
    
    def register_tools(self):
        pass
        
    async def run(self) -> None:
        pass
"""

@pytest.fixture
def temp_external_plugins_dir():
    # Setup temp directory
    temp_dir = tempfile.mkdtemp()
    old_env = os.environ.get("NUKE_EXTERNAL_PLUGINS_DIR")
    os.environ["NUKE_EXTERNAL_PLUGINS_DIR"] = temp_dir
    
    yield Path(temp_dir)
    
    # Teardown
    shutil.rmtree(temp_dir, ignore_errors=True)
    if old_env is not None:
        os.environ["NUKE_EXTERNAL_PLUGINS_DIR"] = old_env
    else:
        os.environ.pop("NUKE_EXTERNAL_PLUGINS_DIR", None)

def test_load_external_plugins(temp_external_plugins_dir):
    # Write a test plugin file
    plugin_path = temp_external_plugins_dir / "test_mock_plugin.py"
    with open(plugin_path, "w", encoding="utf-8") as f:
        f.write(MOCK_PLUGIN_CONTENT)
        
    # Discover plugins
    registry.discover()
    
    # Assert it was loaded and registered
    assert "mock_external_executor" in registry._registry
    executor = registry._registry["mock_external_executor"]
    assert executor.display_name == "Mock External Executor"
    
    # Clean up registry
    registry._registry.pop("mock_external_executor", None)
