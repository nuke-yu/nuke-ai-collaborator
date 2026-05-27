import aiosqlite
import json
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "chat.db")

def get_db():
    return aiosqlite.connect(DB_PATH)

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                type TEXT NOT NULL CHECK(type IN ('human', 'bot')),
                role TEXT,
                system_prompt TEXT,
                avatar_color TEXT DEFAULT '#6366f1',
                FOREIGN KEY (group_id) REFERENCES groups(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                member_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (group_id) REFERENCES groups(id),
                FOREIGN KEY (member_id) REFERENCES members(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS role_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                summary TEXT NOT NULL,
                covered_through_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS message_embeddings (
                message_id INTEGER PRIMARY KEY,
                embedding TEXT NOT NULL,
                FOREIGN KEY (message_id) REFERENCES messages(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS member_read (
                member_id INTEGER NOT NULL,
                group_id INTEGER NOT NULL,
                last_read_id INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (member_id, group_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS message_reactions (
                message_id INTEGER NOT NULL,
                member_id INTEGER NOT NULL,
                emoji TEXT NOT NULL,
                PRIMARY KEY (message_id, member_id, emoji),
                FOREIGN KEY (message_id) REFERENCES messages(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS pinned_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                pinned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(group_id, message_id),
                FOREIGN KEY (message_id) REFERENCES messages(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS role_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                role TEXT NOT NULL,
                system_prompt TEXT NOT NULL,
                avatar_color TEXT DEFAULT '#6366f1'
            )
        """)
        for col_sql in [
            "ALTER TABLE messages ADD COLUMN reply_to_id INTEGER",
            "ALTER TABLE messages ADD COLUMN edited_at TIMESTAMP",
            "ALTER TABLE messages ADD COLUMN is_deleted INTEGER DEFAULT 0",
            "ALTER TABLE messages ADD COLUMN file_url TEXT",
            "ALTER TABLE messages ADD COLUMN file_name TEXT",
            "ALTER TABLE messages ADD COLUMN file_size INTEGER",
            "ALTER TABLE messages ADD COLUMN file_type TEXT",
            "ALTER TABLE members ADD COLUMN model_provider TEXT DEFAULT 'deepseek'",
            "ALTER TABLE members ADD COLUMN model_name TEXT DEFAULT 'deepseek-chat'",
            "ALTER TABLE members ADD COLUMN auto_reply TEXT DEFAULT NULL",
            "ALTER TABLE groups ADD COLUMN announcement TEXT DEFAULT NULL",
            "ALTER TABLE messages ADD COLUMN is_auto_reply INTEGER DEFAULT 0",
            "ALTER TABLE members ADD COLUMN context_cleared_at TEXT DEFAULT NULL",
            "ALTER TABLE members ADD COLUMN temperature REAL DEFAULT 0.7",
            "ALTER TABLE members ADD COLUMN max_tokens INTEGER DEFAULT 4096",
            "ALTER TABLE role_summaries ADD COLUMN bot_id INTEGER DEFAULT NULL",
            "ALTER TABLE members ADD COLUMN personality_prompt TEXT DEFAULT NULL",
            "ALTER TABLE members ADD COLUMN executor_id TEXT DEFAULT 'simple_v1'",
            "ALTER TABLE members ADD COLUMN executor_config TEXT DEFAULT '{}'",
            "ALTER TABLE members ADD COLUMN done_keyword TEXT DEFAULT NULL",
        ]:
            try:
                await db.execute(col_sql)
                await db.commit()
            except Exception:
                pass
        await _seed_templates(db)
        await db.commit()

_DEFAULT_TEMPLATES = [
    ("代码助手", "代码助手", "#3b82f6",
     "你是一位全栈代码助手，精通 Python、JavaScript、TypeScript、Go、Java 等主流语言。"
     "当用户提出编程问题时，给出简洁、可运行的代码示例，并简要解释关键逻辑。"
     "遇到 bug 时先分析根因，再给出修复方案。回答用中文，代码用对应语言。"),

    ("翻译专家", "翻译专家", "#10b981",
     "你是一位专业翻译，擅长中英文互译及多语种翻译。"
     "翻译时保持原文语气和风格，技术文档注重准确性，文学内容注重流畅自然。"
     "如有歧义，给出多个候选译文并说明差异。直接输出翻译结果，无需解释。"),

    ("写作助手", "写作助手", "#f59e0b",
     "你是一位写作助手，擅长各类文体：商业文案、技术博客、产品文档、邮件、报告。"
     "根据用户提供的主题和要求，输出结构清晰、语言流畅的内容。"
     "可以润色已有文稿，也可以从零起草。输出中文，风格专业而不失亲切。"),

    ("架构师", "系统架构师", "#8b5cf6",
     "你是一位资深系统架构师，有10年以上大型分布式系统设计经验。"
     "擅长微服务架构、领域驱动设计（DDD）、高可用高并发方案设计。"
     "当用户描述业务需求时，给出架构方案、技术选型建议和关键设计决策，"
     "并说明各方案的优缺点和适用场景。回答简洁专业，必要时用图示（ASCII）辅助说明。"),

    ("后端 Java 工程师", "后端Java工程师", "#ef4444",
     "你是一位资深 Java 后端工程师，精通 Spring Boot / Spring Cloud 生态、"
     "MyBatis/JPA、Kafka、Redis、分布式事务等技术栈。"
     "擅长解决性能调优、并发问题、数据库优化、微服务拆分等实际工程问题。"
     "回答包含具体的 Java 代码示例，遵循最佳实践，说明设计意图。"),

    ("前端工程师", "前端工程师", "#ec4899",
     "你是一位资深前端工程师，精通 React / Vue / TypeScript，熟悉 Webpack/Vite 工程化，"
     "了解 CSS 架构、性能优化、无障碍设计。"
     "当用户提出前端问题时，给出可运行的组件代码和样式方案，"
     "并解释关键实现思路。注重代码可维护性和用户体验。"),

    ("运维工程师", "DevOps工程师", "#6366f1",
     "你是一位 DevOps / SRE 工程师，精通 Linux 运维、Docker、Kubernetes、CI/CD 流水线、"
     "Prometheus / Grafana 监控体系，熟悉 AWS / 阿里云等主流云平台。"
     "当用户提出运维问题时，给出具体的命令、配置文件或脚本，"
     "并说明操作的目的和注意事项。注重稳定性和安全性。"),

    ("需求分析师", "需求分析师", "#0ea5e9",
     "你是一位专业的产品需求分析师，擅长将模糊的业务诉求转化为清晰的产品需求。"
     "当用户描述功能想法时，输出：功能目标、用户故事（User Story）、"
     "验收标准（AC）、边界条件和优先级建议。"
     "格式规范，逻辑严密，站在用户和业务价值的角度思考。"),

    ("测试专家", "QA测试工程师", "#14b8a6",
     "你是一位 QA 测试专家，擅长测试用例设计（等价类、边界值、场景法）、"
     "接口测试（Postman/pytest）、自动化测试（Selenium/Playwright）和性能测试。"
     "当用户描述功能时，输出完整的测试用例矩阵，包含正向、异常、边界场景，"
     "并标注优先级。发现潜在问题时主动提出质疑。"),

    ("后端 Python 专家", "后端Python专家", "#84cc16",
     "你是一位资深 Python 后端工程师，精通 FastAPI / Django / Flask，"
     "熟悉 SQLAlchemy、Celery、Redis、异步编程（asyncio）和 Python 性能优化。"
     "擅长解决实际工程问题：数据库设计、API 设计、并发处理、代码重构。"
     "回答包含可运行的 Python 代码，遵循 PEP8，注重代码质量和可测试性。"),
]

async def _seed_templates(db):
    async with db.execute("SELECT name FROM role_templates") as cur:
        existing = {row[0] for row in await cur.fetchall()}
    for name, role, color, prompt in _DEFAULT_TEMPLATES:
        if name not in existing:
            await db.execute(
                "INSERT INTO role_templates (name, role, system_prompt, avatar_color) VALUES (?,?,?,?)",
                (name, role, prompt, color)
            )
    await db.commit()

async def get_group(db, group_id: int):
    async with db.execute("SELECT * FROM groups WHERE id = ?", (group_id,)) as cur:
        row = await cur.fetchone()
        if row:
            return {"id": row[0], "name": row[1], "created_at": row[2],
                    "announcement": row[3] if len(row) > 3 else None}
        return None

def _parse_json(val):
    try:
        return json.loads(val) if val else {}
    except Exception:
        return {}


def _row_to_member(r):
    return {"id": r[0], "group_id": r[1], "name": r[2], "type": r[3],
            "role": r[4], "system_prompt": r[5], "avatar_color": r[6],
            "model_provider": r[7] if len(r) > 7 else "deepseek",
            "model_name": r[8] if len(r) > 8 else "deepseek-chat",
            "auto_reply": r[9] if len(r) > 9 else None,
            "context_cleared_at": r[10] if len(r) > 10 else None,
            "temperature": r[11] if len(r) > 11 else 0.7,
            "max_tokens": r[12] if len(r) > 12 else 4096,
            "personality_prompt": r[13] if len(r) > 13 else None,
            "executor_id": r[14] if len(r) > 14 else "simple_v1",
            "executor_config": _parse_json(r[15]) if len(r) > 15 else {},
            "done_keyword": r[16] if len(r) > 16 else None}

async def get_members(db, group_id: int):
    async with db.execute("SELECT * FROM members WHERE group_id = ?", (group_id,)) as cur:
        rows = await cur.fetchall()
        return [_row_to_member(r) for r in rows]

_MSG_SQL = """
    SELECT m.id, m.group_id, m.member_id, m.content, m.created_at,
           mb.name, mb.type, mb.avatar_color,
           m.reply_to_id, rm.content, rmb.name,
           m.edited_at, m.is_deleted,
           m.file_url, m.file_name, m.file_size, m.file_type,
           m.is_auto_reply
    FROM messages m
    JOIN members mb ON m.member_id = mb.id
    LEFT JOIN messages rm ON m.reply_to_id = rm.id
    LEFT JOIN members rmb ON rm.member_id = rmb.id
"""

def _row_to_msg(r):
    reply_to = {"id": r[8], "sender_name": r[10], "content": r[9]} if r[8] else None
    created_at = r[4]
    if created_at and "Z" not in created_at and "+" not in created_at:
        created_at = created_at.replace(" ", "T") + "Z"
    return {"id": r[0], "group_id": r[1], "member_id": r[2], "content": r[3],
            "created_at": created_at, "sender_name": r[5], "sender_type": r[6],
            "avatar_color": r[7], "reply_to": reply_to,
            "edited_at": r[11], "is_deleted": bool(r[12]),
            "file_url": r[13], "file_name": r[14], "file_size": r[15], "file_type": r[16],
            "is_auto_reply": bool(r[17]) if len(r) > 17 else False}

async def get_messages(db, group_id: int, limit: int = 50, before_id: int = None, after_time: str = None):
    if before_id:
        async with db.execute(
            _MSG_SQL + "WHERE m.group_id = ? AND m.id < ? ORDER BY m.id DESC LIMIT ?",
            (group_id, before_id, limit)
        ) as cur:
            rows = await cur.fetchall()
    elif after_time:
        async with db.execute(
            _MSG_SQL + "WHERE m.group_id = ? AND m.created_at > ? ORDER BY m.id DESC LIMIT ?",
            (group_id, after_time, limit)
        ) as cur:
            rows = await cur.fetchall()
    else:
        async with db.execute(
            _MSG_SQL + "WHERE m.group_id = ? ORDER BY m.id DESC LIMIT ?",
            (group_id, limit)
        ) as cur:
            rows = await cur.fetchall()
    return list(reversed([_row_to_msg(r) for r in rows]))

async def get_all_messages(db, group_id: int):
    async with db.execute(
        _MSG_SQL + "WHERE m.group_id = ? ORDER BY m.id ASC",
        (group_id,)
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_msg(r) for r in rows]

async def get_member_stats(db, group_id: int):
    async with db.execute(
        """SELECT m.name, m.type, COUNT(msg.id) as cnt
           FROM members m
           LEFT JOIN messages msg ON msg.member_id = m.id AND msg.group_id = ? AND (msg.is_deleted IS NULL OR msg.is_deleted = 0)
           WHERE m.group_id = ?
           GROUP BY m.id ORDER BY cnt DESC""",
        (group_id, group_id)
    ) as cur:
        rows = await cur.fetchall()
    return [{"name": r[0], "type": r[1], "count": r[2]} for r in rows]

async def save_message(db, group_id: int, member_id: int, content: str, reply_to_id: int = None,
                       file_url: str = None, file_name: str = None, file_size: int = None, file_type: str = None,
                       is_auto_reply: bool = False):
    async with db.execute(
        "INSERT INTO messages (group_id, member_id, content, reply_to_id, file_url, file_name, file_size, file_type, is_auto_reply) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (group_id, member_id, content, reply_to_id, file_url, file_name, file_size, file_type, int(is_auto_reply))
    ) as cur:
        await db.commit()
        return cur.lastrowid

async def update_member_setting(db, member_id: int, auto_reply: str | None):
    await db.execute("UPDATE members SET auto_reply=? WHERE id=?", (auto_reply or None, member_id))
    await db.commit()

async def clear_bot_context(db, member_id: int, group_id: int):
    from datetime import datetime
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    await db.execute("UPDATE members SET context_cleared_at=? WHERE id=?", (now, member_id))
    await db.execute("DELETE FROM role_summaries WHERE bot_id=?", (member_id,))
    await db.commit()

async def update_member_full(db, member_id: int, data: dict):
    executor_config = data.get('executor_config', {})
    if not isinstance(executor_config, str):
        executor_config = json.dumps(executor_config)
    await db.execute(
        "UPDATE members SET name=?, role=?, system_prompt=?, avatar_color=?, model_provider=?, model_name=?, temperature=?, max_tokens=?, personality_prompt=?, executor_id=?, executor_config=?, done_keyword=? WHERE id=?",
        (data.get('name'), data.get('role'), data.get('system_prompt'),
         data.get('avatar_color'), data.get('model_provider'), data.get('model_name'),
         data.get('temperature', 0.7), data.get('max_tokens', 4096),
         data.get('personality_prompt') or None,
         data.get('executor_id', 'simple_v1'), executor_config,
         data.get('done_keyword') or None,
         member_id)
    )
    await db.commit()

async def update_message(db, msg_id: int, content: str):
    await db.execute(
        "UPDATE messages SET content=?, edited_at=CURRENT_TIMESTAMP WHERE id=?",
        (content, msg_id)
    )
    await db.commit()

async def soft_delete_message(db, msg_id: int):
    await db.execute("UPDATE messages SET is_deleted=1 WHERE id=?", (msg_id,))
    await db.commit()

async def get_message_meta(db, msg_id: int):
    async with db.execute("SELECT id, group_id, member_id FROM messages WHERE id=?", (msg_id,)) as cur:
        row = await cur.fetchone()
        return {"id": row[0], "group_id": row[1], "member_id": row[2]} if row else None

async def toggle_reaction(db, message_id: int, member_id: int, emoji: str):
    async with db.execute(
        "SELECT 1 FROM message_reactions WHERE message_id=? AND member_id=? AND emoji=?",
        (message_id, member_id, emoji)
    ) as cur:
        exists = await cur.fetchone()
    if exists:
        await db.execute(
            "DELETE FROM message_reactions WHERE message_id=? AND member_id=? AND emoji=?",
            (message_id, member_id, emoji)
        )
    else:
        await db.execute(
            "INSERT INTO message_reactions (message_id, member_id, emoji) VALUES (?,?,?)",
            (message_id, member_id, emoji)
        )
    await db.commit()

async def get_reactions_for_message(db, message_id: int) -> dict:
    async with db.execute(
        "SELECT emoji, member_id FROM message_reactions WHERE message_id=?", (message_id,)
    ) as cur:
        rows = await cur.fetchall()
    result = {}
    for emoji, mid in rows:
        result.setdefault(emoji, []).append(mid)
    return result

async def get_reactions_for_group(db, group_id: int) -> dict:
    async with db.execute("""
        SELECT mr.message_id, mr.emoji, mr.member_id
        FROM message_reactions mr
        JOIN messages m ON mr.message_id = m.id
        WHERE m.group_id = ?
    """, (group_id,)) as cur:
        rows = await cur.fetchall()
    result = {}
    for msg_id, emoji, mid in rows:
        result.setdefault(str(msg_id), {}).setdefault(emoji, []).append(mid)
    return result

async def pin_message(db, group_id: int, message_id: int):
    await db.execute(
        "INSERT OR IGNORE INTO pinned_messages (group_id, message_id) VALUES (?, ?)",
        (group_id, message_id)
    )
    await db.commit()

async def unpin_message(db, group_id: int, message_id: int):
    await db.execute(
        "DELETE FROM pinned_messages WHERE group_id=? AND message_id=?",
        (group_id, message_id)
    )
    await db.commit()

async def get_pinned_messages(db, group_id: int):
    async with db.execute(
        _MSG_SQL + """
        JOIN pinned_messages pm ON m.id = pm.message_id
        WHERE pm.group_id = ?
        ORDER BY pm.pinned_at DESC
        """,
        (group_id,)
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_msg(r) for r in rows]

async def get_member(db, member_id: int):
    async with db.execute("SELECT * FROM members WHERE id = ?", (member_id,)) as cur:
        row = await cur.fetchone()
        return _row_to_member(row) if row else None
