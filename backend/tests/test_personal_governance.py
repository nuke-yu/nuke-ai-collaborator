"""Unit tests for Personal Knowledge Vault governance and deletion safety."""
import logging
import os
import tempfile
import unittest

import db
from ai.personal_vault import (
    add_record,
    delete_vault,
    evaluate_access_control_rule,
    is_personal_app_active,
    list_acl_audit_events,
    project,
    register_personal_app,
    set_personal_app_status,
    set_access_control_rule,
)
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

    async def test_abac_wildcard_and_specific_deny_precedence(self) -> None:
        await set_access_control_rule(
            user_id=self.user_id,
            subject_type="user",
            subject_id="*",
            object_type="group",
            object_id="7",
            effect="deny",
        )
        self.assertFalse(
            await evaluate_access_control_rule(
                user_id=self.user_id,
                subject_type="user",
                subject_id="42",
                object_type="group",
                object_id="7",
            )
        )
        await set_access_control_rule(
            user_id=self.user_id,
            subject_type="user",
            subject_id="42",
            object_type="group",
            object_id="7",
            effect="allow",
        )
        self.assertTrue(
            await evaluate_access_control_rule(
                user_id=self.user_id,
                subject_type="user",
                subject_id="42",
                object_type="group",
                object_id="7",
            )
        )

    async def test_personal_app_lifecycle_is_user_isolated(self) -> None:
        await register_personal_app(user_id=self.user_id, app_id="chat", name="Chat")
        self.assertTrue(await is_personal_app_active(user_id=self.user_id, app_id="chat"))
        self.assertFalse(await is_personal_app_active(user_id=998, app_id="chat"))
        self.assertTrue(await set_personal_app_status(user_id=self.user_id, app_id="chat", active=False))
        self.assertFalse(await is_personal_app_active(user_id=self.user_id, app_id="chat"))
        self.assertFalse(await set_personal_app_status(user_id=self.user_id, app_id="missing", active=True))

    async def test_inactive_app_cannot_project_or_read(self) -> None:
        record_id = await add_record(
            user_id=self.user_id, kind="preference", content="dark mode",
            source_type="manual", authority="user_statement", explicit=True,
        )
        await register_personal_app(user_id=self.user_id, app_id="chat", name="Chat")
        projection_id = await project(
            user_id=self.user_id, record_id=record_id, group_id=7, bot_id=3,
            purpose="assistant_context", app_id="chat",
        )
        self.assertTrue(projection_id.startswith("projection:"))
        await set_personal_app_status(user_id=self.user_id, app_id="chat", active=False)
        with self.assertRaisesRegex(ValueError, "inactive"):
            await project(
                user_id=self.user_id, record_id=record_id, group_id=7, bot_id=3,
                purpose="assistant_context", app_id="chat",
            )

    async def test_acl_audit_listing_contains_no_memory_content(self) -> None:
        from ai.personal_vault import record_acl_audit_event

        await record_acl_audit_event(
            user_id=self.user_id, actor_id="user:999", scope_kind="personal",
            group_id=None, bot_id=None, action="read", allowed=False,
            reason="denied by policy",
        )
        events = await list_acl_audit_events(user_id=self.user_id)
        self.assertEqual(events[0]["action"], "read")
        self.assertNotIn("content", events[0])


if __name__ == "__main__":
    unittest.main()
