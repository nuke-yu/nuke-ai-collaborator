from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from workspace.observation import ObservationStore, file_version, observation_scope, get_observation_store


class ObservationStoreTest(unittest.TestCase):
    def test_file_version_hashes_in_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "large.bin"
            payload = b"0123456789" * 300_000
            path.write_bytes(payload)
            self.assertEqual(file_version(path), hashlib.sha256(payload).hexdigest())

    def test_store_can_be_injected_per_context(self) -> None:
        original = get_observation_store()
        injected = ObservationStore()
        with observation_scope(injected):
            self.assertIs(get_observation_store(), injected)
        self.assertIs(get_observation_store(), original)


if __name__ == "__main__":
    unittest.main()
