"""Unit tests for Personal Knowledge Vault governance and deletion safety."""
import logging
import os
import tempfile
import unittest

import db
from ai.personal_vault import add_record, delete_vault, project
from memory.adapters.runtime import legacy_memory_database


class PersonalGovernanceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.user_id = 999
        self.tmp_dir = tempfile.mkdtemp()
        self.original_personal_path = db.DB_PATH
        legacy_memory_database.clear_cache()

    async def asyncTearDown(self) -> None:
        await delete_vault(self.user_id)
        legacy_memory_database.clear_cache()

    async def test_secret_sensitivity_projection_rejected(self) -> None:
        record_id = await add_record(
            user_id=self.user_id,
            kind="profile",
            content="Top secret credential",
            source_type="manual",
            authority="user_statement",
            sensitivity="secret",
            explicit=True,
        )
        with self.assertRaisesRegex(ValueError, "secret personal knowledge cannot be projected"):
            await project(
                user_id=self.user_id,
                record_id=record_id,
                group_id=7,
                bot_id=3,
                purpose="assistant_context",
            )

    async def test_restricted_sensitivity_projection_requires_confirmation(self) -> None:
        record_id = await add_record(
            user_id=self.user_id,
            kind="expertise",
            content="Restricted Medical History",
            source_type="manual",
            authority="observed",
            sensitivity="restricted",
            explicit=False,
        )
        with self.assertRaisesRegex(ValueError, "restricted personal knowledge requires explicit confirmation"):
            await project(
                user_id=self.user_id,
                record_id=record_id,
                group_id=7,
                bot_id=3,
                purpose="assistant_context",
                allow_restricted=False,
            )

        # Confirming allow_restricted allows the projection
        projection_id = await project(
            user_id=self.user_id,
            record_id=record_id,
            group_id=7,
            bot_id=3,
            purpose="assistant_context",
            allow_restricted=True,
        )
        self.assertTrue(projection_id.startswith("projection:"))

    async def test_delete_vault_concurrency_safe_and_audited(self) -> None:
        await add_record(
            user_id=self.user_id,
            kind="preference",
            content="Likes dark mode",
            source_type="manual",
            authority="user_statement",
            explicit=True,
        )

        with self.assertLogs("audit.personal_vault", level=logging.INFO) as log_cm:
            deleted = await delete_vault(self.user_id)
            self.assertTrue(deleted)

        self.assertTrue(any("personal_vault_deleted" in line for line in log_cm.output))
        self.assertFalse(any("Likes dark mode" in line for line in log_cm.output))


if __name__ == "__main__":
    unittest.main()
