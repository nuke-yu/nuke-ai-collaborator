import asyncio
from db import init_db, get_db

async def seed():
    await init_db()
    async with get_db() as db:
        async with db.execute("SELECT COUNT(*) FROM groups") as cur:
            count = (await cur.fetchone())[0]
        if count > 0:
            print("已有数据，跳过 seed")
            return

        async with db.execute("INSERT INTO groups (name) VALUES (?)", ("项目开发群",)) as cur:
            group_id = cur.lastrowid

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
            await db.execute(
                "INSERT INTO members (group_id, name, type, role, system_prompt, avatar_color) VALUES (?,?,?,?,?,?)",
                (group_id, name, type_, role, prompt, color)
            )

        await db.commit()
        print(f"✅ 创建群组 ID={group_id}，添加 3 个 AI 角色")

asyncio.run(seed())
