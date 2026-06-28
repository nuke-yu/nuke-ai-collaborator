import os
import tempfile
import json
import pytest
from fastapi.testclient import TestClient

from main import app
from core import auth as _auth

# Mock authentication to allow requests in test
async def mock_get_current_user():
    return {"uid": 1, "sub": "test"}

@pytest.fixture
def client():
    app.dependency_overrides[_auth.get_current_user] = mock_get_current_user
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()

@pytest.fixture
def temp_mcp_config():
    # Setup temporary file path
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    
    # Store old env var
    old_val = os.environ.get("MCP_SERVERS_CONFIG")
    os.environ["MCP_SERVERS_CONFIG"] = path
    
    # Write empty config initially
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"mcpServers": {}}, f)
        
    yield path
    
    # Teardown
    if os.path.exists(path):
        os.remove(path)
    if old_val is not None:
        os.environ["MCP_SERVERS_CONFIG"] = old_val
    else:
        os.environ.pop("MCP_SERVERS_CONFIG", None)

def test_get_mcp_config(client, temp_mcp_config):
    response = client.get("/api/config/mcp")
    assert response.status_code == 200
    assert response.json() == {"mcpServers": {}}

def test_save_mcp_config_success(client, temp_mcp_config):
    new_config = {
        "mcpServers": {
          "test-server": {
            "command": "node",
            "args": ["test.js"],
            "enabled": True
          }
        }
    }
    response = client.put("/api/config/mcp", json=new_config)
    assert response.status_code == 200
    assert response.json() == new_config
    
    # Verify file was written
    with open(temp_mcp_config, "r", encoding="utf-8") as f:
        file_data = json.load(f)
    assert file_data == new_config

def test_save_mcp_config_validation_error(client, temp_mcp_config):
    # Invalid config format (missing mcpServers)
    invalid_config = {
        "invalidKey": {}
    }
    response = client.put("/api/config/mcp", json=invalid_config)
    assert response.status_code == 400
    assert "Invalid MCP config format" in response.json()["detail"]
