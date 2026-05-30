from db.migrations import run_migrations

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


async def init_db():
    # Lazy import avoids circular dependency at import time while respecting
    # any DB_PATH override set before calling (e.g. test fixtures).
    import db as _db
    async with _db.connect() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT NOT NULL,
                announcement TEXT DEFAULT NULL,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS members (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id         INTEGER NOT NULL,
                name             TEXT NOT NULL,
                type             TEXT NOT NULL CHECK(type IN ('human', 'bot')),
                role             TEXT,
                system_prompt    TEXT,
                avatar_color     TEXT    DEFAULT '#6366f1',
                model_provider   TEXT    DEFAULT 'deepseek',
                model_name       TEXT    DEFAULT 'deepseek-chat',
                auto_reply       TEXT    DEFAULT NULL,
                context_cleared_at TEXT  DEFAULT NULL,
                temperature      REAL    DEFAULT 0.7,
                max_tokens       INTEGER DEFAULT 4096,
                personality_prompt TEXT  DEFAULT NULL,
                executor_id      TEXT    DEFAULT 'simple_v1',
                executor_config  TEXT    DEFAULT '{}',
                done_keyword     TEXT    DEFAULT NULL,
                FOREIGN KEY (group_id) REFERENCES groups(id)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id    INTEGER NOT NULL,
                member_id   INTEGER NOT NULL,
                content     TEXT    NOT NULL,
                reply_to_id INTEGER DEFAULT NULL,
                edited_at   TIMESTAMP DEFAULT NULL,
                is_deleted  INTEGER DEFAULT 0,
                file_url    TEXT    DEFAULT NULL,
                file_name   TEXT    DEFAULT NULL,
                file_size   INTEGER DEFAULT NULL,
                file_type   TEXT    DEFAULT NULL,
                is_auto_reply INTEGER DEFAULT 0,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (group_id)  REFERENCES groups(id),
                FOREIGN KEY (member_id) REFERENCES members(id)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS role_summaries (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id           INTEGER NOT NULL,
                role               TEXT    NOT NULL,
                summary            TEXT    NOT NULL,
                covered_through_id INTEGER NOT NULL,
                bot_id             INTEGER DEFAULT NULL,
                created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS message_embeddings (
                message_id INTEGER PRIMARY KEY,
                embedding  TEXT NOT NULL,
                FOREIGN KEY (message_id) REFERENCES messages(id)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS member_read (
                member_id    INTEGER NOT NULL,
                group_id     INTEGER NOT NULL,
                last_read_id INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (member_id, group_id)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS message_reactions (
                message_id INTEGER NOT NULL,
                member_id  INTEGER NOT NULL,
                emoji      TEXT    NOT NULL,
                PRIMARY KEY (message_id, member_id, emoji),
                FOREIGN KEY (message_id) REFERENCES messages(id)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS pinned_messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id   INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                pinned_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(group_id, message_id),
                FOREIGN KEY (message_id) REFERENCES messages(id)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS role_templates (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT NOT NULL,
                role         TEXT NOT NULL,
                system_prompt TEXT NOT NULL,
                avatar_color TEXT DEFAULT '#6366f1'
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS permission_rules (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_id       INTEGER NOT NULL,
                tool_pattern TEXT    NOT NULL,
                args_pattern TEXT    NOT NULL DEFAULT '',
                action       TEXT    NOT NULL DEFAULT 'allow',
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (bot_id) REFERENCES members(id)
            )
        """)
        await conn.commit()
        await run_migrations(conn)
        await _seed_templates(conn)
        await conn.commit()
