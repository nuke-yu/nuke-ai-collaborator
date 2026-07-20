import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai.personal_vault import add_record, connect, project, projected_context
from runtime.dbpaths import personal_db_path


class PersonalVaultTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root_patch=patch("skills.constants.WORKSPACE_ROOT",self.tmp.name)
        self.root_patch.start()

    async def asyncTearDown(self):
        self.root_patch.stop(); self.tmp.cleanup()

    async def test_personal_db_is_outside_group_storage(self):
        record_id=await add_record(user_id=4,kind="expertise",content="I know distributed systems",
                                   source_type="chat",source_id="m1",explicit=True,authority="user_statement")
        self.assertTrue(Path(personal_db_path(4)).exists())
        self.assertIn("_personal/user_4",personal_db_path(4))
        self.assertNotIn("group_",personal_db_path(4))
        async with connect(4) as db:
            async with db.execute("SELECT status FROM personal_records WHERE record_id=?",(record_id,)) as cur:
                self.assertEqual((await cur.fetchone())[0],"active")

    async def test_projection_is_group_bot_and_purpose_scoped(self):
        record_id=await add_record(user_id=4,kind="preference",content="Use concise reports",
                                   source_type="chat",explicit=True,authority="user_statement")
        await project(user_id=4,record_id=record_id,group_id=10,bot_id=2,purpose="assistant_context")
        self.assertEqual(len(await projected_context(user_id=4,group_id=10,bot_id=2,purpose="assistant_context")),1)
        self.assertEqual(await projected_context(user_id=4,group_id=11,bot_id=2,purpose="assistant_context"),[])
        self.assertEqual(await projected_context(user_id=4,group_id=10,bot_id=3,purpose="assistant_context"),[])

    async def test_secret_cannot_be_projected_and_observation_is_provisional(self):
        record_id=await add_record(user_id=4,kind="workflow",content="token AKIAIOSFODNN7EXAMPLE",
                                   source_type="email",sensitivity="secret")
        with self.assertRaises(ValueError):
            await project(user_id=4,record_id=record_id,group_id=1,bot_id=None,purpose="assistant_context")
        async with connect(4) as db:
            async with db.execute("SELECT content,status FROM personal_records WHERE record_id=?",(record_id,)) as cur:
                content,status=await cur.fetchone()
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE",content); self.assertEqual(status,"provisional")
