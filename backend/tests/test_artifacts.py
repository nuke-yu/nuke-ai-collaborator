"""Unit tests for Unified Artifact Manager."""

import unittest
import asyncio
import os
import shutil
import tempfile
import db as _db

from artifacts import (
    Artifact,
    ArtifactNotFoundError,
    ArtifactOrigin,
    ArtifactScope,
    calculate_checksum,
    delete_artifact,
    get_artifact,
    list_artifacts,
    register_artifact,
)


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


if __name__ == "__main__":
    unittest.main()
