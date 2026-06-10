"""DFT-035: pluggable, config-driven embedding backend.

Unit tests deliberately avoid instantiating the local MiniLM model (an ~80MB
download) — local paths are asserted by signature only, API paths via an
injected HTTP client, and reindex via tiny fake EmbeddingFunctions over a temp
chroma dir.
"""
import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chromadb.api.types import EmbeddingFunction

from ai import embeddings as emb


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload
    def raise_for_status(self):
        pass
    def json(self):
        return self._payload


class _FakeHttp:
    """Records the last POST and returns a canned embeddings payload."""
    def __init__(self, payload):
        self._payload = payload
        self.calls = []
    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return _FakeResponse(self._payload)


class _FixedDimEF(EmbeddingFunction):
    """A deterministic EF of a chosen dimension, for reindex tests."""
    def __init__(self, dim):
        self._dim = dim
    def __call__(self, input):
        return [[float(len(t))] * self._dim for t in input]


class TestSignature(unittest.TestCase):
    def test_local_signature(self):
        self.assertEqual(emb.embedding_signature(provider="local"), "local:default")

    def test_openai_default_model_signature(self):
        self.assertEqual(emb.embedding_signature(provider="openai"),
                         "openai:text-embedding-3-small")

    def test_deepseek_default_model_signature(self):
        self.assertEqual(emb.embedding_signature(provider="deepseek"),
                         "deepseek:text-embedding-v2")

    def test_explicit_model_signature(self):
        self.assertEqual(emb.embedding_signature(provider="openai", model="text-embedding-3-large"),
                         "openai:text-embedding-3-large")

    def test_unknown_provider_raises(self):
        with self.assertRaises(emb.EmbeddingConfigError):
            emb.embedding_signature(provider="bogus")


class TestApiEmbedding(unittest.TestCase):
    def test_builds_request_and_parses(self):
        http = _FakeHttp({"data": [{"index": 0, "embedding": [0.1, 0.2]}]})
        out = emb._embed_via_api(
            http=http, base_url="https://api.openai.com/v1/embeddings",
            api_key="sk-test", model="text-embedding-3-small", docs=["hello"], timeout=30)
        self.assertEqual(out, [[0.1, 0.2]])
        call = http.calls[0]
        self.assertEqual(call["url"], "https://api.openai.com/v1/embeddings")
        self.assertEqual(call["headers"]["Authorization"], "Bearer sk-test")
        self.assertEqual(call["json"]["model"], "text-embedding-3-small")
        self.assertEqual(call["json"]["input"], ["hello"])

    def test_reorders_by_index(self):
        # API may return out of order; result must follow input order.
        http = _FakeHttp({"data": [
            {"index": 1, "embedding": [9.0]},
            {"index": 0, "embedding": [1.0]},
        ]})
        out = emb._embed_via_api(http=http, base_url="u", api_key="k", model="m",
                                 docs=["a", "b"], timeout=30)
        self.assertEqual(out, [[1.0], [9.0]])

    def test_empty_input_no_call(self):
        http = _FakeHttp({"data": []})
        out = emb._embed_via_api(http=http, base_url="u", api_key="k", model="m",
                                 docs=[], timeout=30)
        self.assertEqual(out, [])
        self.assertEqual(http.calls, [])

    def test_function_is_chromadb_embedding_function(self):
        ef = emb.ApiEmbeddingFunction(base_url="u", api_key="k", model="m")
        self.assertIsInstance(ef, EmbeddingFunction)


class TestFactory(unittest.TestCase):
    def test_unknown_provider_raises(self):
        with self.assertRaises(emb.EmbeddingConfigError):
            emb.get_embedding_function(provider="bogus")

    def test_api_provider_requires_key(self):
        with patch("config.get_key", return_value=""):
            with self.assertRaises(emb.EmbeddingConfigError):
                emb.get_embedding_function(provider="openai")

    def test_api_provider_builds_function(self):
        with patch("config.get_key", return_value="sk-xyz"):
            ef = emb.get_embedding_function(provider="openai")
        self.assertIsInstance(ef, emb.ApiEmbeddingFunction)
        self.assertEqual(ef._model, "text-embedding-3-small")
        self.assertEqual(ef._base_url, "https://api.openai.com/v1/embeddings")


class TestVerifySignature(unittest.TestCase):
    def test_match_ok(self):
        emb.verify_signature("openai:text-embedding-3-small", "openai:text-embedding-3-small")

    def test_legacy_none_assumed_local(self):
        # A pre-DFT-035 collection has no stamp → treated as local default.
        emb.verify_signature(None, "local:default")  # no raise

    def test_legacy_none_against_api_raises(self):
        with self.assertRaises(emb.EmbeddingModelMismatchError):
            emb.verify_signature(None, "openai:text-embedding-3-small")

    def test_mismatch_raises(self):
        with self.assertRaises(emb.EmbeddingModelMismatchError):
            emb.verify_signature("local:default", "openai:text-embedding-3-small")


class TestReindex(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def test_reindex_changes_dimension_and_preserves_docs(self):
        import chromadb
        client = chromadb.PersistentClient(path=self.dir)
        old = client.get_or_create_collection(
            name="messages", embedding_function=_FixedDimEF(3),
            metadata={"hnsw:space": "cosine", "emb_sig": "local:default"})
        old.upsert(ids=["1", "2"], documents=["alpha", "beta"],
                   metadatas=[{"bot_id": 7}, {"bot_id": 7}])
        del old, client

        n = emb.reindex_collection(
            self.dir, new_ef=_FixedDimEF(5), new_sig="openai:text-embedding-3-small",
            old_ef=_FixedDimEF(3))
        self.assertEqual(n, 2)

        client2 = chromadb.PersistentClient(path=self.dir)
        col = client2.get_or_create_collection(
            name="messages", embedding_function=_FixedDimEF(5))
        self.assertEqual(dict(col.metadata).get("emb_sig"), "openai:text-embedding-3-small")
        got = col.get(include=["documents"])
        self.assertEqual(set(got["ids"]), {"1", "2"})
        # New vectors must be 5-dim now.
        peek = col.get(ids=["1"], include=["embeddings"])
        self.assertEqual(len(peek["embeddings"][0]), 5)


if __name__ == "__main__":
    unittest.main()
