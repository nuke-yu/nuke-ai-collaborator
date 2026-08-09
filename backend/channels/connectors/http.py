"""Shared outbound HTTP boundary adapted from OpenHanako bridge/outbound-http."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Mapping, Protocol
from urllib.parse import urlsplit

import httpx

from executors.redaction import redact_secrets


@dataclass(frozen=True, slots=True)
class ConnectorHttpResponse:
    status: int
    body: Any
    headers: Mapping[str, str]


class ConnectorHttpTransport(Protocol):
    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Any,
        timeout: float,
    ) -> ConnectorHttpResponse: ...


class HttpxConnectorTransport:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(follow_redirects=False)
        self._owns_client = client is None

    async def request(self, method, url, *, headers, json_body, timeout):
        response = await self._client.request(
            method, url, headers=dict(headers), json=json_body, timeout=timeout
        )
        try:
            body = response.json()
        except ValueError:
            body = response.text
        return ConnectorHttpResponse(response.status_code, body, dict(response.headers))

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class ConnectorHttpError(RuntimeError):
    def __init__(self, platform: str, stage: str, message: str, *, status: int | None = None, attempts: int = 1):
        host = ""
        self.platform = platform
        self.stage = stage
        self.status = status
        self.attempts = attempts
        super().__init__(redact_secrets(f"[{platform}:{stage}] {message}")[0])


class ConnectorHttpClient:
    def __init__(
        self,
        platform: str,
        transport: ConnectorHttpTransport | None = None,
        *,
        timeout: float = 30.0,
        retry_delay: float = 1.0,
    ) -> None:
        self.platform = str(platform or "").strip().lower()
        if not self.platform or timeout <= 0 or retry_delay < 0:
            raise ValueError("platform and positive HTTP limits are required")
        self.transport = transport or HttpxConnectorTransport()
        self.timeout = timeout
        self.retry_delay = retry_delay

    async def request_json(
        self,
        stage: str,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        json_body: Any = None,
        idempotent: bool = False,
        max_retries: int = 2,
    ) -> ConnectorHttpResponse:
        if not str(stage or "").strip() or urlsplit(url).scheme not in {"http", "https"}:
            raise ValueError("connector HTTP stage and absolute URL are required")
        attempts = max_retries + 1 if idempotent else 1
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                response = await self.transport.request(
                    method.upper(), url, headers=headers or {}, json_body=json_body,
                    timeout=self.timeout,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt < attempts:
                    await asyncio.sleep(self.retry_delay * (2 ** (attempt - 1)))
                    continue
                raise ConnectorHttpError(
                    self.platform, stage, f"network request failed: {type(exc).__name__}",
                    attempts=attempt,
                ) from exc
            if response.status in {429} or response.status >= 500:
                if attempt < attempts:
                    await asyncio.sleep(self.retry_delay * (2 ** (attempt - 1)))
                    continue
            if response.status < 200 or response.status >= 300:
                raise ConnectorHttpError(
                    self.platform, stage, f"HTTP {response.status}",
                    status=response.status, attempts=attempt,
                )
            return response
        raise ConnectorHttpError(self.platform, stage, f"request failed: {last_error}")

    async def close(self) -> None:
        close = getattr(self.transport, "close", None)
        if close is not None:
            await close()
