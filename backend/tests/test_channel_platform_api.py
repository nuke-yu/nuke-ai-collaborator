from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.channels import router
from channels.connectors import ConnectorAuthError, FeishuWebhookResult


class _Platforms:
    def __init__(self, result=None, error=None):
        self.result = result or FeishuWebhookResult()
        self.error = error
        self.calls = []

    async def ingest_feishu(self, instance_id, payload, *, raw_body, headers):
        self.calls.append((instance_id, payload, raw_body, headers))
        if self.error:
            raise self.error
        return self.result


def _client(platforms):
    app = FastAPI()
    app.state.channel_platform = platforms
    app.include_router(router)
    return TestClient(app)


def test_feishu_webhook_returns_authenticated_challenge():
    platforms = _Platforms(FeishuWebhookResult(challenge="challenge-1"))
    response = _client(platforms).post(
        "/api/channels/webhooks/feishu/feishu:prod",
        json={"type": "url_verification", "challenge": "challenge-1"},
    )
    assert response.status_code == 200
    assert response.json() == {"challenge": "challenge-1"}
    assert platforms.calls[0][0] == "feishu:prod"


def test_feishu_webhook_rejects_auth_failure_without_leaking_detail():
    platforms = _Platforms(error=ConnectorAuthError(
        "Authorization: Bearer real-platform-secret"
    ))
    response = _client(platforms).post(
        "/api/channels/webhooks/feishu/feishu:prod",
        json={"schema": "2.0"},
    )
    assert response.status_code == 401
    assert "real-platform-secret" not in response.text


def test_feishu_webhook_rejects_oversized_body_before_parsing():
    response = _client(_Platforms()).post(
        "/api/channels/webhooks/feishu/feishu:prod",
        content=b"{" + b"x" * 256_001,
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413
