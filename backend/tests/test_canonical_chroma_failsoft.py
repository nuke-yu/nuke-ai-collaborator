"""Contract tests for the canonical Chroma projection boundary."""

import unittest
from unittest.mock import patch

from memory.adapters.projections import chroma_client


class _FakePanic(BaseException):
    __module__ = "pyo3_runtime"
    __qualname__ = "PanicException"


class TestCanonicalChromaFailSoft(unittest.TestCase):
    def setUp(self):
        chroma_client._client = None
        chroma_client._collection = None

    def tearDown(self):
        chroma_client._client = None
        chroma_client._collection = None

    def test_library_panic_becomes_retryable_runtime_error(self):
        with patch("chromadb.PersistentClient", side_effect=_FakePanic("boom")):
            with self.assertRaises(RuntimeError) as context:
                chroma_client._get_collection()
        self.assertNotIsInstance(context.exception, _FakePanic)
        self.assertIsNone(chroma_client._client)
        self.assertIsNone(chroma_client._collection)

    def test_control_flow_exceptions_are_not_swallowed(self):
        for exception_type in (KeyboardInterrupt, SystemExit):
            with self.subTest(exception_type=exception_type):
                chroma_client._client = None
                chroma_client._collection = None
                with patch("chromadb.PersistentClient", side_effect=exception_type()):
                    with self.assertRaises(exception_type):
                        chroma_client._get_collection()


if __name__ == "__main__":
    unittest.main()
