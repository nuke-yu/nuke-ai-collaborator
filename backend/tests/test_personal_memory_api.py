"""Unit tests for Personal Knowledge Vault API endpoints."""

import unittest
from unittest.mock import patch
import asyncio
import os
import shutil
import tempfile
import db as _db
from ai.personal_vault import (
    add_record,
    project,
    export_vault,
    delete_record,
    revoke_projection,
)


class TestPersonalMemoryAPI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.user_id = 99
        self.patch_db = patch("runtime.dbpaths.personal_db_path", return_value=os.path.join(self.tmp_dir, "personal_99.db"))
        self.patch_db.start()

    async def asyncTearDown(self):
        self.patch_db.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    async def test_add_and_delete_record(self):
        rec_id = await add_record(
            user_id=self.user_id,
            kind="preference",
            content="I prefer dark mode in all IDEs",
            source_type="manual",
            source_id="test",
            speaker="user:99",
            subject="99",
            authority="user_statement",
            sensitivity="private",
            confidence=1.0,
            explicit=True,
        )

        vault = await export_vault(self.user_id)
        records = vault.get("records", [])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["record_id"], rec_id)

        # Delete specific record
        deleted = await delete_record(self.user_id, rec_id)
        self.assertTrue(deleted)

        # Confirm vault is empty
        vault_after = await export_vault(self.user_id)
        self.assertEqual(len(vault_after.get("records", [])), 0)

    async def test_create_and_revoke_projection(self):
        rec_id = await add_record(
            user_id=self.user_id,
            kind="habit",
            content="Preferred model: deepseek-chat",
            source_type="manual",
            source_id="test",
            speaker="user:99",
            subject="99",
            authority="user_statement",
            sensitivity="private",
            confidence=1.0,
            explicit=True,
        )

        proj_id = await project(
            user_id=self.user_id,
            record_id=rec_id,
            group_id=10,
            bot_id=2,
            purpose="assistant_context",
        )

        vault = await export_vault(self.user_id)
        projections = vault.get("projections", [])
        self.assertEqual(len(projections), 1)
        self.assertEqual(projections[0]["projection_id"], proj_id)

        # Revoke projection
        revoked = await revoke_projection(self.user_id, proj_id)
        self.assertTrue(revoked)

        vault_after = await export_vault(self.user_id)
        self.assertEqual(len(vault_after.get("projections", [])), 0)


if __name__ == "__main__":
    unittest.main()
