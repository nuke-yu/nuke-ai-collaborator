import json
import os
import tempfile

from fastapi.testclient import TestClient

from main import app
import config as app_config
from core import auth as _auth


def _client_with_user(user=None):
    app.dependency_overrides.clear()
    if user is not None:
        app.dependency_overrides[_auth.get_current_user] = lambda: user
    return TestClient(app)


def test_system_status_requires_authentication():
    client = _client_with_user()
    try:
        response = client.get("/api/system/status")
        assert response.status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_system_status_rejects_non_operator():
    client = _client_with_user({"uid": 1, "sub": "user"})
    try:
        response = client.get("/api/system/status")
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_system_status_allows_operator():
    client = _client_with_user({"uid": 1, "sub": "ops", "is_operator": True})
    try:
        response = client.get("/api/system/status")
        assert response.status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_api_config_requires_authentication():
    client = _client_with_user()
    try:
        response = client.get("/api/config")
        assert response.status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_api_config_rejects_non_operator():
    client = _client_with_user({"uid": 1, "sub": "user"})
    try:
        response = client.get("/api/config")
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_api_config_allows_operator_without_returning_secret_values(monkeypatch, tmp_path):
    monkeypatch.setattr(app_config, "CONFIG_PATH", tmp_path / "app_config.json")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-value-that-must-not-be-returned")
    app_config.bootstrap_from_env()
    client = _client_with_user({"uid": 1, "sub": "ops", "is_operator": True})
    try:
        response = client.get("/api/config")
        assert response.status_code == 200
        body = response.json()
        assert body["deepseek_api_key"]["configured"] is True
        assert "secret-value-that-must-not-be-returned" not in response.text
    finally:
        app.dependency_overrides.clear()


def test_mcp_config_rejects_non_operator():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    old_val = os.environ.get("MCP_SERVERS_CONFIG")
    os.environ["MCP_SERVERS_CONFIG"] = path
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"mcpServers": {}}, f)
    client = _client_with_user({"uid": 1, "sub": "user"})
    try:
        response = client.get("/api/config/mcp")
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()
        if os.path.exists(path):
            os.remove(path)
        if old_val is not None:
            os.environ["MCP_SERVERS_CONFIG"] = old_val
        else:
            os.environ.pop("MCP_SERVERS_CONFIG", None)
