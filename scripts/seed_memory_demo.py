#!/usr/bin/env python3
"""Seed synthetic memory data into a Group & Personal Vault for instant testing."""
import argparse
import asyncio
import hashlib
import json
import os
import sys
import time

# Add backend directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from ai.memory import _memory_db
from memory.bootstrap import build_group_knowledge_client
from memory.contracts import IngestGroupFact, RecallGroupFacts
from memory.domain import MemoryScope, ScopeKind, Principal
from ai.skill_learning import recall_skills
from ai.personal_vault import add_record, project, format_projected_context


async def seed_demo_data(group_id: int, user_id: int, bot_id: int):
    print(f"\n🚀 [Memory Fast-Test Seed] Group ID: {group_id}, User ID: {user_id}, Bot ID: {bot_id}\n")

    from memory.infrastructure import MemorySchemaManager
    from memory.adapters.runtime import legacy_memory_database
    await MemorySchemaManager(legacy_memory_database).ensure_group(group_id)

    group_client = build_group_knowledge_client()
    scope = MemoryScope(kind=ScopeKind.GROUP, group_id=group_id, actor_id=f"user:{user_id}")

    # 1. Ingest Canonical Group Facts
    print("1️⃣ Seeding Canonical Group Facts...")
    facts = [
        "Project Architecture: Supervisor -> Worker -> MCP Collector process topology",
        "Frontend Tech Stack: React 19, Vite, Tailwind CSS v4",
        "Backend Tech Stack: Python 3.13, FastAPI, SQLite, aiosqlite",
        "Database Deployment Rule: Group isolation with per-group SQLite files",
    ]
    for fact in facts:
        rec_id = await group_client.ingest_fact(
            IngestGroupFact(
                scope=scope,
                source_type="user_explicit",
                source_id=f"seed-{int(time.time()*1000)}",
                subject_key="tech_stack",
                statement=fact,
            )
        )
        print(f"   ✓ Added Group Fact: {rec_id} -> {fact[:40]}...")

    # 2. Ingest Learned Skills
    print("\n2️⃣ Seeding Learned Skills...")
    skills = [
        ("deploy_fastapi_app", "Deploy FastAPI service to production Docker container", "stable"),
        ("run_pytest_suite", "Run backend unit test suite with PYTHONPATH=backend pytest", "active"),
        ("migrate_sqlite_schema", "Run SQLite versioned DDL migration scripts", "trial"),
    ]
    async with await _memory_db("skills", group_id, write=True) as db:
        for name, trigger, maturity in skills:
            skill_id = f"skill:{hashlib.sha256(f'{group_id}:{bot_id}:{name}'.encode()).hexdigest()[:24]}"
            declaration = json.dumps({
                "name": name,
                "trigger": trigger,
                "risk_level": "S0",
                "procedure": [f"Step 1: Execute {name}", "Step 2: Verify outcome"],
            })
            digest = hashlib.sha256(declaration.encode()).hexdigest()
            now = int(time.time() * 1000)

            await db.execute(
                """INSERT INTO skills (skill_id, group_id, bot_id, name, maturity, risk_level, current_version, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'S0', 1, ?, ?)
                   ON CONFLICT(skill_id) DO UPDATE SET maturity=excluded.maturity, updated_at=excluded.updated_at""",
                (skill_id, group_id, bot_id, name, maturity, now, now),
            )
            await db.execute(
                """INSERT INTO skill_versions (skill_id, version, declaration_json, content_hash, evidence_ids, created_at)
                   VALUES (?, 1, ?, ?, '["seed-001"]', ?)
                   ON CONFLICT(skill_id, version) DO NOTHING""",
                (skill_id, declaration, digest, now),
            )
            print(f"   ✓ Added Skill [{maturity}]: {skill_id} -> {name}")
        await db.commit()

    # 3. Ingest Personal Knowledge Vault Data
    print("\n3️⃣ Seeding Personal Knowledge Vault...")
    p_id = await add_record(
        user_id=user_id,
        kind="preference",
        content="Prefers concise GitHub markdown output and async Python code",
        source_type="user_statement",
        source_id="pref-001",
        authority="user_statement",
        sensitivity="private",
        explicit=True,
    )
    print(f"   ✓ Added Personal Vault Record: {p_id}")

    await project(
        user_id=user_id,
        group_id=group_id,
        bot_id=bot_id,
        record_id=p_id,
        purpose="assistant_context",
    )
    print(f"   ✓ Projected Personal Record {p_id} -> Group {group_id}")

    # 4. Instant Recall Verification
    print("\n" + "="*60)
    print("🔍 Instant Memory Recall Test Output:")
    print("="*60)

    # Test FTS5 Group Fact Recall
    recall_res = await group_client.recall_facts(
        RecallGroupFacts(
            scope=scope,
            query="FastAPI React SQLite",
        )
    )
    print(f"\n[FTS5 Group Fact Recall Results] Found {len(recall_res.hits)} matching facts:")
    for f in recall_res.hits:
        print(f"  • [{f.kind}] {f.content}")

    # Test Bounded Skill Recall
    skill_text, skill_ids = await recall_skills(
        group_id=group_id,
        bot_id=bot_id,
        query="pytest test suite execution",
        run_id="demo-run-001",
    )
    print(f"\n[Skill Recall Results] Matched Skills: {skill_ids}")
    if skill_text:
        print(f"  Prompt Injection Preview:\n{skill_text[:200]}...")

    # Test Personal Vault Projection
    p_ctx = await format_projected_context(
        user_id=user_id,
        group_id=group_id,
        bot_id=bot_id,
        char_budget=500,
    )
    print(f"\n[Personal Context Injection Results]:\n{p_ctx}")

    print("\n✅ Fast-Test Seed Completed Successfully!\n")


def main():
    parser = argparse.ArgumentParser(description="Seed synthetic memory for instant testing.")
    parser.add_argument("--group-id", type=int, default=1, help="Target Group ID (default: 1)")
    parser.add_argument("--user-id", type=int, default=1, help="Target User ID (default: 1)")
    parser.add_argument("--bot-id", type=int, default=1, help="Target Bot ID (default: 1)")
    args = parser.parse_args()

    asyncio.run(seed_demo_data(args.group_id, args.user_id, args.bot_id))


if __name__ == "__main__":
    main()
