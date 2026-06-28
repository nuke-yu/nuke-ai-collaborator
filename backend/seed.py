import asyncio
import os
import shutil
import sqlite3
from pathlib import Path
from db import init_db, get_db
from workspace import init_group_workspace, init_bot_workspace

async def seed():
    # 1. Seed workspaces templates & system directories if they don't exist in the mounted NUKE_WORKSPACE_ROOT
    nuke_ws_root = os.environ.get("NUKE_WORKSPACE_ROOT")
    if nuke_ws_root:
        dest_root = Path(nuke_ws_root)
        src_root = Path("/app/backend/workspaces")
        if src_root.exists() and src_root.resolve() != dest_root.resolve():
            dest_root.mkdir(parents=True, exist_ok=True)
            for folder in ["system", "templates"]:
                src_folder = src_root / folder
                dest_folder = dest_root / folder
                if src_folder.exists() and not dest_folder.exists():
                    print(f"Seeding {folder} from {src_folder} to {dest_folder}...")
                    try:
                        shutil.copytree(src_folder, dest_folder, symlinks=True)
                        print(f"✅ Successfully seeded {folder}")
                    except Exception as e:
                        print(f"❌ Failed to seed {folder}: {e}")

    # 2. Database seeding
    await init_db()
    async with get_db() as db:
        db.row_factory = sqlite3.Row
        
        async with db.execute("SELECT COUNT(*) FROM groups") as cur:
            row = await cur.fetchone()
            count = row[0]
        if count > 0:
            print("已有数据，跳过 seed")
            return

        async with db.execute("INSERT INTO groups (name) VALUES (?)", ("项目开发群",)) as cur:
            group_id = cur.lastrowid

        # Initialize the group workspace directories and copy the templates/roles
        await init_group_workspace(group_id, "项目开发群")

        bots = [
            ("需求分析师", "bot", "需求分析师",
             "你是一位专业的需求分析师。当用户提到需求、功能、分析等话题时，负责拆解需求，输出清晰的功能点和技术规格。回答简洁专业，用中文。",
             "#8b5cf6"),
            ("开发工程师", "bot", "开发工程师",
             "你是一位经验丰富的全栈开发工程师。当用户提到开发、代码、实现、bug等话题时，给出技术方案和代码实现。回答实用，包含具体代码示例，用中文。",
             "#3b82f6"),
            ("测试工程师", "bot", "测试工程师",
             "你是一位严格的QA测试工程师。当用户提到测试、质量、bug、验证等话题时，设计测试用例，找出潜在问题。回答详细，包含测试用例，用中文。",
             "#10b981"),
        ]

        for name, type_, role, prompt, color in bots:
            async with db.execute(
                "INSERT INTO members (group_id, name, type, role, system_prompt, avatar_color) VALUES (?,?,?,?,?,?)",
                (group_id, name, type_, role, prompt, color)
            ) as cur:
                bot_id = cur.lastrowid
            
            # Fetch the inserted bot as a dict for init_bot_workspace
            async with db.execute("SELECT * FROM members WHERE id=?", (bot_id,)) as cur:
                row = await cur.fetchone()
                bot_dict = dict(row)
            
            # Initialize bot workspace files (IDENTITY.md, SOUL.md, AGENT.md, etc.)
            await init_bot_workspace(bot_dict)

        await db.commit()
        print(f"✅ 创建群组 ID={group_id}，并完成所有成员的工作区初始化")

asyncio.run(seed())
