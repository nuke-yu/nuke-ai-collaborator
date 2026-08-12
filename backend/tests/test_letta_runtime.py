import os
import tempfile
from unittest.mock import patch

import pytest

import db
from memory.application.letta_runtime import (
    configure_database, evict_memory_blocks, read_memory_blocks, write_memory_block,
)


@pytest.mark.asyncio
async def test_letta_runtime_is_group_scoped_and_evicts_low_value_blocks():
    path = tempfile.mktemp(suffix="_letta.db")
    try:
        class _Database:
            def connect(self, *_args, **_kwargs):
                return db.connect(path)
        configure_database(_Database())
        try:
            await write_memory_block(group_id=7, bot_id=3, content="python deployment", importance=0.9)
            await write_memory_block(group_id=7, bot_id=3, content="old note", importance=0.1)
            await write_memory_block(group_id=8, bot_id=3, content="python deployment", importance=1.0)
            found = await read_memory_blocks(group_id=7, bot_id=3, query="python", limit=2)
            assert [item["content"] for item in found] == ["python deployment"]
            assert await evict_memory_blocks(group_id=7, bot_id=3, keep=1) == 1
        finally:
            configure_database(None)
    finally:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(path + suffix)
            except FileNotFoundError:
                pass
