"""Collector-side orchestration of MCP OAuth flows (McpAuthTool style).

The mcp SDK's OAuthClientProvider drives the protocol; this object bridges its
two callbacks to the bus so the flow is interactive across processes:

  - redirect_handler(url): the SDK hands us the authorization URL (it embeds the
    `state`). We surface the URL (resolve the begin() future → returned to the
    bot as the mcp_authenticate result) and register a callback future keyed by
    that state.
  - callback_handler(): the SDK awaits the (code, state) pair. We await the
    callback future for this server's pending state — resolved when the web
    callback arrives over the bus (resolve_callback).

One in-flight auth per server (keyed by server for the redirect→callback handoff,
by state for the inbound callback correlation).
"""
import asyncio
from urllib.parse import urlparse, parse_qs


class MCPAuthFlows:
    def __init__(self):
        self._url_fut: dict[str, asyncio.Future] = {}      # server -> Future[str]
        self._state_by_server: dict[str, str] = {}         # server -> state
        self._cb_fut: dict[str, asyncio.Future] = {}       # state  -> Future[(code, state)]

    def _abort_server(self, server: str, reason: str) -> None:
        """Fail any in-flight flow for `server` before starting a replacement.

        This keeps a stale redirect future or callback future from hanging
        around if the caller retries the same server flow after a timeout or a
        manual restart."""
        state = self._state_by_server.pop(server, None)
        url_fut = self._url_fut.pop(server, None)
        if url_fut and not url_fut.done():
            url_fut.set_exception(RuntimeError(reason))
        if state:
            cb = self._cb_fut.pop(state, None)
            if cb and not cb.done():
                cb.set_exception(RuntimeError(reason))

    def begin(self, server: str) -> asyncio.Future:
        """Register intent to authenticate `server`; returns a future that
        resolves to the authorization URL once the SDK produces it."""
        self._abort_server(server, f"replaced by a new OAuth flow for server '{server}'")
        fut = asyncio.get_running_loop().create_future()
        self._url_fut[server] = fut
        return fut

    def redirect_handler_for(self, server: str):
        async def handler(url: str) -> None:
            state = (parse_qs(urlparse(url).query).get("state") or [None])[0]
            if state:
                self._state_by_server[server] = state
                self._cb_fut[state] = asyncio.get_running_loop().create_future()
            f = self._url_fut.get(server)
            if f and not f.done():
                f.set_result(url)
        return handler

    def callback_handler_for(self, server: str):
        async def handler() -> tuple[str, str | None]:
            state = self._state_by_server.get(server)
            fut = self._cb_fut.get(state) if state else None
            if fut is None:
                raise RuntimeError(f"no pending OAuth callback for server '{server}'")
            try:
                code, st = await fut
                return code, st
            finally:
                self._cb_fut.pop(state, None)
                self._state_by_server.pop(server, None)
        return handler

    def resolve_callback(self, state: str, code: str) -> bool:
        """Called when the web callback delivers (code, state) over the bus.
        Returns True if it matched a pending flow."""
        fut = self._cb_fut.get(state)
        if fut and not fut.done():
            fut.set_result((code, state))
            return True
        return False

    def fail(self, server: str, reason: str) -> None:
        """Abort a server's in-flight flow (e.g. timeout / user denial)."""
        self._abort_server(server, reason)
