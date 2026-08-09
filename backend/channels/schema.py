"""Single initialization entrypoint for all Channel-owned SQLite tables."""
from __future__ import annotations

from pathlib import Path

from channels.bridge.binding import ChannelBindingStore
from channels.bridge.member import IntegrationMemberStore
from channels.stores import ChannelStore


async def initialize_channel_schema(path: str | Path) -> None:
    """Create the complete Channel schema for a fresh deployment.

    All tables share one Channel-owned database, but each store retains its
    own DDL ownership.  This composition root is the only startup call needed
    by Supervisor and prevents partial initialization ordering bugs.
    """
    path = str(path)
    await ChannelStore(path).initialize()
    await ChannelBindingStore(path).initialize()
    await IntegrationMemberStore(path).initialize()
