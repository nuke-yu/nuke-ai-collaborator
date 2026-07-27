"""Deterministic exact and structured-semantic task identities."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TaskIdentity:
    normalized_task: str
    exact_signature: str
    semantic_cluster_key: str
    family: str
    concepts: tuple[str, ...]
    file_extensions: tuple[str, ...]


_FAMILIES = (
    ("repair", ("fix", "repair", "debug", "resolve", "修复", "解决", "排查")),
    ("refactor", ("refactor", "restructure", "重构", "整理架构")),
    ("implement", ("implement", "create", "develop", "add", "新增", "实现", "开发")),
    ("verify", ("test", "verify", "validate", "测试", "验证", "检查")),
    ("build", ("build", "compile", "package", "构建", "编译", "打包")),
    ("document", ("document", "docs", "readme", "文档", "说明")),
)

_CONCEPTS = {
    "database": ("database", "db", "sqlite", "数据库"),
    "migration": ("migration", "migrate", "迁移"),
    "schema": ("schema", "表结构", "模式"),
    "memory": ("memory", "记忆", "内存"),
    "api": ("api", "endpoint", "接口"),
    "authentication": ("auth", "authentication", "jwt", "登录", "认证"),
    "authorization": ("permission", "authorization", "权限", "鉴权"),
    "backend": ("backend", "server", "fastapi", "后端", "服务端"),
    "frontend": ("frontend", "react", "vite", "前端"),
    "ui": ("ui", "layout", "页面", "界面"),
    "test": ("test", "pytest", "测试"),
    "build": ("build", "compile", "构建", "编译"),
    "dependency": ("dependency", "package", "依赖"),
    "cache": ("cache", "缓存"),
    "network": ("network", "http", "websocket", "网络"),
    "projection": ("projection", "投影"),
    "outbox": ("outbox",),
    "workflow": ("workflow", "工作流"),
    "skill": ("skill", "技能"),
}

_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "for",
        "in",
        "of",
        "on",
        "the",
        "to",
        "with",
        "issue",
        "problem",
        "please",
        "fix",
        "repair",
        "debug",
        "resolve",
        "implement",
        "create",
        "develop",
        "add",
        "test",
        "verify",
        "validate",
    }
)


def identify_task(task: str) -> TaskIdentity:
    normalized = re.sub(r"\s+", " ", (task or "").strip().lower())[:1000]
    exact_signature = hashlib.sha256(normalized.encode()).hexdigest()[:24]
    family = _classify_family(normalized)
    concepts = tuple(
        concept
        for concept, aliases in _CONCEPTS.items()
        if any(_contains_alias(normalized, alias) for alias in aliases)
    )
    extensions = tuple(
        sorted(
            {
                "." + match.lower()
                for match in re.findall(
                    r"(?:^|[/\\\w.-])\.([a-z0-9]{1,10})(?=$|[\s,:;)])",
                    normalized,
                )
            }
        )
    )
    fallback_terms: tuple[str, ...] = ()
    if not concepts:
        fallback_terms = tuple(
            sorted(
                {
                    token
                    for token in re.findall(r"[a-z][a-z0-9_-]{2,}", normalized)
                    if token not in _STOPWORDS and not token.isdigit()
                }
            )[:6]
        )
    semantic_payload = {
        "family": family,
        "concepts": concepts,
        "extensions": extensions,
        "fallback_terms": fallback_terms,
    }
    semantic_cluster_key = hashlib.sha256(
        json.dumps(
            semantic_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()[:24]
    return TaskIdentity(
        normalized_task=normalized,
        exact_signature=exact_signature,
        semantic_cluster_key=semantic_cluster_key,
        family=family,
        concepts=concepts,
        file_extensions=extensions,
    )


def _classify_family(normalized: str) -> str:
    for family, aliases in _FAMILIES:
        if any(_contains_alias(normalized, alias) for alias in aliases):
            return family
    return "other"


def _contains_alias(value: str, alias: str) -> bool:
    if re.fullmatch(r"[a-z0-9_-]+", alias):
        return re.search(rf"(?<![a-z0-9_]){re.escape(alias)}(?![a-z0-9_])", value) is not None
    return alias in value
