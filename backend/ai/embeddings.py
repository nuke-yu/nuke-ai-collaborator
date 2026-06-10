"""DFT-035: pluggable, config-driven embedding backend.

The real RAG path (``ai/memory.py``) embeds documents through a chromadb
``EmbeddingFunction``. Historically the model was hardcoded — the local MiniLM
default in memory.py, and a dead DeepSeek call in ``ai/client.py``. This module
centralises provider/model selection so the embedding model is driven by config
instead of literals, and wraps OpenAI-compatible API providers (OpenAI,
DeepSeek) as chromadb-compatible embedding functions.

Dimension guard
---------------
A chroma collection's vectors are fixed at the model that created them. Switching
provider/model changes the dimension and invalidates every stored vector, so the
active ``provider:model`` signature is stamped into the collection metadata
(``emb_sig``) and verified on load (see ``ai/memory.py``); a mismatch refuses to
serve stale results and points at the reindex script.
"""
import logging

import httpx
from chromadb.api.types import EmbeddingFunction, Documents, Embeddings

from core import config

log = logging.getLogger(__name__)

LOCAL_DEFAULT_SIG = "local:default"

# provider -> (default_model, default_base_url, app_config key field)
_PROVIDER_DEFAULTS = {
    "openai":   ("text-embedding-3-small", "https://api.openai.com/v1/embeddings", "openai_api_key"),
    "deepseek": ("text-embedding-v2",      "https://api.deepseek.com/v1/embeddings", "deepseek_api_key"),
}


class EmbeddingConfigError(Exception):
    """Provider/model/key misconfiguration."""


class EmbeddingModelMismatchError(Exception):
    """The persisted vector index was built with a different embedding model."""
    def __init__(self, stored: str, configured: str):
        self.stored = stored
        self.configured = configured
        super().__init__(
            f"向量索引由 '{stored}' 构建，但当前配置为 '{configured}'。"
            f"维度不兼容，需重建索引：python3 -m scripts.reindex_embeddings"
        )


# ── shared sync HTTP client (chromadb EF.__call__ is synchronous) ─────────
_http: "httpx.Client | None" = None


def _get_http() -> httpx.Client:
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.Client(
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10))
    return _http


def close_embedding_client() -> None:
    """Close the shared sync client; call on app shutdown."""
    global _http
    if _http is not None and not _http.is_closed:
        _http.close()
    _http = None


# ── provider resolution ───────────────────────────────────────────────────
def _active_provider(provider: str | None) -> str:
    return (provider or config.EMBEDDING_PROVIDER or "local").lower()


def _active_model(provider: str, model: str | None) -> str:
    if provider == "local":
        return "default"
    return model or config.EMBEDDING_MODEL or _PROVIDER_DEFAULTS[provider][0]


def _validate_provider(provider: str) -> None:
    if provider != "local" and provider not in _PROVIDER_DEFAULTS:
        raise EmbeddingConfigError(
            f"未知 embedding provider: {provider!r}（支持 local/openai/deepseek）")


def embedding_signature(provider: str | None = None, model: str | None = None) -> str:
    """Return the ``provider:model`` stamp for the active (or given) config."""
    p = _active_provider(provider)
    _validate_provider(p)
    return f"{p}:{_active_model(p, model)}"


# ── API-backed embedding function (OpenAI-compatible) ─────────────────────
def _embed_via_api(*, http, base_url: str, api_key: str, model: str,
                   docs: list, timeout: float) -> list:
    """Call an OpenAI-compatible /embeddings endpoint; returns row-ordered vectors.

    Pure (no chromadb wrapping) so it is directly unit-testable. The API may
    return rows out of order, so results are sorted back to input order by index.
    """
    if not docs:
        return []
    resp = http.post(
        base_url,
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": model, "input": docs, "encoding_format": "float"},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = sorted(resp.json()["data"], key=lambda d: d.get("index", 0))
    return [d["embedding"] for d in data]


class ApiEmbeddingFunction(EmbeddingFunction):
    """Embeds via an OpenAI-compatible ``/embeddings`` endpoint.

    Works for OpenAI and DeepSeek (same request/response shape). The call is
    synchronous because chromadb invokes embedding functions synchronously;
    ``ai/memory.py`` already offloads chroma operations to a thread pool.
    """
    def __init__(self, *, base_url: str, api_key: str, model: str,
                 http_client: "httpx.Client | None" = None, timeout: float = 30.0):
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._http = http_client
        self._timeout = timeout

    def __call__(self, input: Documents) -> Embeddings:
        return _embed_via_api(
            http=self._http or _get_http(), base_url=self._base_url,
            api_key=self._api_key, model=self._model,
            docs=list(input), timeout=self._timeout)

    @staticmethod
    def name() -> str:
        return "nuke_api_embedding"


def get_embedding_function(provider: str | None = None, model: str | None = None,
                           endpoint: str | None = None) -> EmbeddingFunction:
    """Build the chromadb embedding function for the active (or given) config."""
    p = _active_provider(provider)
    _validate_provider(p)
    if p == "local":
        # chromadb's bundled MiniLM (offline, no key, 384-dim).
        from chromadb.utils import embedding_functions
        return embedding_functions.DefaultEmbeddingFunction()

    d_model, d_url, key_field = _PROVIDER_DEFAULTS[p]
    base_url = endpoint or config.EMBEDDING_ENDPOINT or d_url
    from config import get_key
    api_key = get_key(key_field)
    if not api_key:
        raise EmbeddingConfigError(
            f"embedding provider '{p}' 需要配置 {key_field}（在设置中填写）")
    return ApiEmbeddingFunction(base_url=base_url, api_key=api_key,
                                model=_active_model(p, model))


def verify_signature(stored: str | None, configured: str) -> None:
    """Raise EmbeddingModelMismatchError if the index model differs from config.

    A ``None`` stamp means a legacy collection predating this stamp; we assume it
    was built with the local default, which is what the codebase shipped before.
    """
    if stored is None:
        stored = LOCAL_DEFAULT_SIG
    if stored != configured:
        raise EmbeddingModelMismatchError(stored, configured)


# ── reindex ───────────────────────────────────────────────────────────────
class _NoOpEmbeddingFunction(EmbeddingFunction):
    """Lets us open a collection just to read documents/metadatas (no embedding)."""
    def __init__(self):
        pass
    def __call__(self, input: Documents) -> Embeddings:
        return [[0.0] for _ in input]
    @staticmethod
    def name() -> str:
        return "nuke_noop_embedding"


def reindex_collection(chroma_path: str, *, new_ef: EmbeddingFunction, new_sig: str,
                       old_ef: "EmbeddingFunction | None" = None,
                       collection_name: str = "messages", batch_size: int = 500) -> int:
    """Re-embed an existing chroma collection with ``new_ef`` and re-stamp it.

    Pulls the stored documents/metadatas (no re-embedding needed to read them),
    drops the collection, recreates it with the new embedding function + signature
    and upserts the documents back — which embeds them with the new model. Returns
    the number of documents reindexed.
    """
    import chromadb
    client = chromadb.PersistentClient(path=chroma_path)
    read_ef = old_ef or _NoOpEmbeddingFunction()
    src = client.get_or_create_collection(collection_name, embedding_function=read_ef)
    snapshot = src.get(include=["documents", "metadatas"])
    ids = snapshot.get("ids") or []
    docs = snapshot.get("documents") or []
    metas = snapshot.get("metadatas") or []
    metas = [m or {} for m in metas]

    client.delete_collection(collection_name)
    dst = client.get_or_create_collection(
        collection_name, embedding_function=new_ef,
        metadata={"hnsw:space": "cosine", "emb_sig": new_sig})

    for i in range(0, len(ids), batch_size):
        dst.upsert(
            ids=ids[i:i + batch_size],
            documents=docs[i:i + batch_size],
            metadatas=metas[i:i + batch_size] if metas else None,
        )
    return len(ids)
