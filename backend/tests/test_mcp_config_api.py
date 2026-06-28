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
    yield TestClient(app)
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
    res_data = response.json()
    res_data.pop("warning", None)
    assert res_data == new_config
    
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


def test_mcp_config_masking(client, temp_mcp_config):
    # 1. Write a config with sensitive info
    original_config = {
        "mcpServers": {
            "test-server": {
                "command": "node",
                "args": ["test.js"],
                "env": {
                    "API_KEY": "supersecret12345",
                    "DEBUG": "true"
                },
                "headers": {
                    "Authorization": "Bearer token1234"
                }
            }
        }
    }
    with open(temp_mcp_config, "w", encoding="utf-8") as f:
        json.dump(original_config, f)

    # 2. GET config should return masked values
    response = client.get("/api/config/mcp")
    assert response.status_code == 200
    res_data = response.json()
    env = res_data["mcpServers"]["test-server"]["env"]
    headers = res_data["mcpServers"]["test-server"]["headers"]
    
    assert env["API_KEY"] == "sup···2345"
    assert env["DEBUG"] == "****"
    assert headers["Authorization"] == "Bea···1234"

    # 3. PUT the same masked config back (simulating user saving without editing key)
    # Let's change args to "test2.js" but keep masked env and headers
    modified_config = res_data
    modified_config["mcpServers"]["test-server"]["args"] = ["test2.js"]
    
    response = client.put("/api/config/mcp", json=modified_config)
    assert response.status_code == 200
    
    # 4. Check the file on disk: original secrets must be restored, but args should be updated
    with open(temp_mcp_config, "r", encoding="utf-8") as f:
        file_data = json.load(f)
        
    server_cfg = file_data["mcpServers"]["test-server"]
    assert server_cfg["args"] == ["test2.js"]
    assert server_cfg["env"]["API_KEY"] == "supersecret12345"
    assert server_cfg["env"]["DEBUG"] == "true"
    assert server_cfg["headers"]["Authorization"] == "Bearer token1234"

    # 5. PUT with modified env key
    modified_config["mcpServers"]["test-server"]["env"]["API_KEY"] = "new_secret_key"
    response = client.put("/api/config/mcp", json=modified_config)
    assert response.status_code == 200
    
    with open(temp_mcp_config, "r", encoding="utf-8") as f:
        file_data = json.load(f)
    assert file_data["mcpServers"]["test-server"]["env"]["API_KEY"] == "new_secret_key"


def test_get_mcp_config_corrupted_json(client, temp_mcp_config):
    with open(temp_mcp_config, "w", encoding="utf-8") as f:
        f.write("{invalid_json:}")
    response = client.get("/api/config/mcp")
    assert response.status_code == 500
    assert "corrupted/invalid JSON" in response.json()["detail"]


def test_get_returns_etag(client, temp_mcp_config):
    response = client.get("/api/config/mcp")
    assert response.status_code == 200
    assert response.headers.get("ETag")


def test_put_with_matching_if_match_succeeds(client, temp_mcp_config):
    etag = client.get("/api/config/mcp").headers["ETag"]
    new_config = {"mcpServers": {"s": {"command": "node", "args": []}}}
    response = client.put("/api/config/mcp", json=new_config, headers={"If-Match": etag})
    assert response.status_code == 200
    # A fresh validator is returned for the just-written content.
    assert response.headers.get("ETag")


def test_put_with_stale_if_match_is_rejected(client, temp_mcp_config):
    stale = client.get("/api/config/mcp").headers["ETag"]
    # Someone else changes the file underneath us.
    with open(temp_mcp_config, "w", encoding="utf-8") as f:
        json.dump({"mcpServers": {"other": {"command": "x", "args": []}}}, f)
    response = client.put(
        "/api/config/mcp",
        json={"mcpServers": {"mine": {"command": "y", "args": []}}},
        headers={"If-Match": stale},
    )
    assert response.status_code == 412
    # The concurrent edit must remain intact (no clobber).
    with open(temp_mcp_config, "r", encoding="utf-8") as f:
        assert json.load(f)["mcpServers"] == {"other": {"command": "x", "args": []}}


def test_put_without_if_match_still_works(client, temp_mcp_config):
    new_config = {"mcpServers": {"s": {"command": "node", "args": []}}}
    response = client.put("/api/config/mcp", json=new_config)
    assert response.status_code == 200
