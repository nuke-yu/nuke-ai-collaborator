"""Unit tests for Unified Artifact Manager."""

import unittest
import asyncio
import io
import os
import shutil
import tempfile
import db as _db
from unittest.mock import AsyncMock, patch
from starlette.datastructures import Headers
from starlette.datastructures import UploadFile

from artifacts import (
    Artifact,
    ArtifactNotFoundError,
    ArtifactOrigin,
    ArtifactScope,
    ArtifactLifecycle,
    calculate_checksum,
    delete_artifact,
    get_artifact,
    list_artifacts,
    register_artifact,
)
from db.schema_split import init_group_db
from runtime.dbpaths import group_db_path
from api.messages import upload_file
from executors.plugins import workspace_tools as wt


class TestArtifactManager(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test_artifacts.db")
        _db.DB_PATH = self.db_path
        await _db.init_db()

    async def asyncTearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    async def test_register_and_get_artifact(self):
        data = b"Hello, Artifact World!"
        checksum = calculate_checksum(data)

        artifact = await register_artifact(
            group_id=1,
            display_name="login_mockup.png",
            origin=ArtifactOrigin.TOOL,
            storage_locator="/workspace/login_mockup.png",
            mime_type="image/png",
            size_bytes=len(data),
            checksum_sha256=checksum,
            session_id="sess_1001",
            bot_id=5,
            authorization_scope=ArtifactScope.GROUP,
            metadata={"width": 800, "height": 600},
        )

        self.assertTrue(artifact.artifact_id.startswith("art_"))
        self.assertEqual(artifact.group_id, 1)
        self.assertEqual(artifact.display_name, "login_mockup.png")
        self.assertEqual(artifact.origin, "tool")
        self.assertEqual(artifact.mime_type, "image/png")
        self.assertEqual(artifact.size_bytes, len(data))
        self.assertEqual(artifact.checksum_sha256, checksum)
        self.assertEqual(artifact.session_id, "sess_1001")
        self.assertEqual(artifact.bot_id, 5)
        self.assertEqual(artifact.metadata, {"width": 800, "height": 600})

        # Fetch artifact by ID
        fetched = await get_artifact(artifact.artifact_id, group_id=1)
        self.assertEqual(fetched.artifact_id, artifact.artifact_id)
        self.assertEqual(fetched.display_name, "login_mockup.png")

    async def test_group_isolation_enforcement(self):
        art = await register_artifact(
            group_id=1,
            display_name="secret_plan.md",
            origin=ArtifactOrigin.WORKSPACE,
            storage_locator="/workspace/secret_plan.md",
        )

        # Attempting to fetch Group 1's artifact under Group 2 should raise ArtifactNotFoundError
        with self.assertRaises(ArtifactNotFoundError):
            await get_artifact(art.artifact_id, group_id=2)

    async def test_artifact_version_derivation_and_soft_delete(self):
        parent = await register_artifact(
            group_id=1,
            display_name="source.md",
            origin=ArtifactOrigin.WORKSPACE,
            storage_locator="/workspace/source.md",
            created_by="bot:1",
        )
        child = await register_artifact(
            group_id=1,
            display_name="report.md",
            origin=ArtifactOrigin.WORKFLOW,
            storage_locator="/workspace/report.md",
            parent_artifact_id=parent.artifact_id,
            derives_from=parent.artifact_id,
            artifact_version=2,
            created_by="bot:2",
        )
        self.assertEqual(child.artifact_version, 2)
        self.assertEqual(child.parent_artifact_id, parent.artifact_id)
        self.assertEqual(child.lifecycle_status, ArtifactLifecycle.ACTIVE)
        self.assertTrue(await delete_artifact(child.artifact_id, 1))
        with self.assertRaises(ArtifactNotFoundError):
            await get_artifact(child.artifact_id, group_id=1)
        remaining = await list_artifacts(group_id=1)
        self.assertEqual([item.artifact_id for item in remaining], [parent.artifact_id])

    async def test_list_artifacts_filtering(self):
        # Register artifacts across different sessions and origins
        art1 = await register_artifact(
            group_id=10,
            display_name="doc1.pdf",
            origin=ArtifactOrigin.UPLOAD,
            storage_locator="/files/doc1.pdf",
            session_id="sess_A",
            bot_id=1,
        )
        art2 = await register_artifact(
            group_id=10,
            display_name="image1.png",
            origin=ArtifactOrigin.TOOL,
            storage_locator="/files/image1.png",
            session_id="sess_A",
            bot_id=2,
        )
        art3 = await register_artifact(
            group_id=10,
            display_name="report.md",
            origin=ArtifactOrigin.WORKFLOW,
            storage_locator="/files/report.md",
            session_id="sess_B",
            bot_id=2,
        )

        # List all for Group 10
        all_arts = await list_artifacts(group_id=10)
        self.assertEqual(len(all_arts), 3)

        # Filter by origin
        upload_arts = await list_artifacts(group_id=10, origin="upload")
        self.assertEqual(len(upload_arts), 1)
        self.assertEqual(upload_arts[0].artifact_id, art1.artifact_id)

        # Filter by session_id
        sess_a_arts = await list_artifacts(group_id=10, session_id="sess_A")
        self.assertEqual(len(sess_a_arts), 2)

        # Filter by bot_id
        bot_2_arts = await list_artifacts(group_id=10, bot_id=2)
        self.assertEqual(len(bot_2_arts), 2)

    async def test_delete_artifact(self):
        art = await register_artifact(
            group_id=1,
            display_name="temp.txt",
            origin=ArtifactOrigin.WORKSPACE,
            storage_locator="/workspace/temp.txt",
        )

        deleted = await delete_artifact(art.artifact_id, group_id=1)
        self.assertTrue(deleted)

        with self.assertRaises(ArtifactNotFoundError):
            await get_artifact(art.artifact_id, group_id=1)


class TestArtifactAutoRegistration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.workspace_patcher = patch("skills.constants.WORKSPACE_ROOT", self.tmp_dir)
        self.workspace_patcher.start()
        self.group_id = 7
        self.group_path = group_db_path(self.group_id)
        os.makedirs(os.path.dirname(self.group_path), exist_ok=True)
        await init_group_db(self.group_path)

    async def asyncTearDown(self):
        self.workspace_patcher.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    async def test_upload_registers_artifact_in_target_group_db(self):
        contents = b"uploaded artifact\n"
        upload = UploadFile(
            file=io.BytesIO(contents),
            filename="notes.txt",
            headers=Headers({"content-type": "text/plain"}),
        )

        result = await upload_file(upload, group_id=self.group_id)

        self.assertIsNotNone(result["artifact_id"])
        with _db.bind_db(self.group_path):
            artifact = await get_artifact(result["artifact_id"], group_id=self.group_id)
        self.assertEqual(artifact.origin, ArtifactOrigin.UPLOAD.value)
        self.assertEqual(artifact.display_name, "notes.txt")
        self.assertEqual(artifact.mime_type, "text/plain")
        self.assertEqual(artifact.size_bytes, len(contents))
        self.assertEqual(artifact.checksum_sha256, calculate_checksum(contents))
        self.assertEqual(artifact.storage_locator, result["url"])

    async def test_write_file_registers_artifact_with_session_and_bot(self):
        with _db.bind_db(self.group_path), patch.object(
            wt._ws, "write_file", new=AsyncMock(return_value="已写入")
        ):
            result = await wt._handle_write_file(
                "src/app.py",
                "print('ok')\n",
                context={
                    "group_id": self.group_id,
                    "bot_id": 42,
                    "session_id": "session-artifact",
                },
            )
            artifacts = await list_artifacts(
                group_id=self.group_id,
                origin=ArtifactOrigin.WORKSPACE.value,
                session_id="session-artifact",
                bot_id=42,
            )

        self.assertIn("已写入", result)
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0].display_name, "app.py")
        self.assertEqual(artifacts[0].storage_locator, "src/app.py")
        self.assertEqual(artifacts[0].checksum_sha256, calculate_checksum(b"print('ok')\n"))


if __name__ == "__main__":
    unittest.main()
