# PRD — AI 功能路线图

> 最后更新：2026-05-28
> 项目：nuke-ai-collaborator

---

## 功能总览（全景一览表）

> ✅ 已完成　⬜ 未做　　优先级：M0-M4 = 里程碑；P1-P4 = 补丁优先级

| # | 维度 | 功能 | 优先级 | 状态 |
|---|------|------|--------|------|
| 1 | 身份 | 角色模板库（一键添加） | M0 | ✅ |
| 2 | 身份 | Bot 编辑（提示词 / 模型） | M0 | ✅ |
| 3 | 人格 | Bot 人格配置（temperature / max_tokens） | M0 | ✅ |
| 4 | 人格 | Bot 性格系统（5 维度滑块 → 行为指令集） | M0 | ✅ |
| 5 | 智能 | 流式输出（打字机效果） | M0 | ✅ |
| 6 | 智能 | 多模型支持（OpenAI / Ollama / Claude） | M0 | ✅ |
| 7 | 智能 | API 限流重试 + fallback_model | P1 | ✅ |
| 8 | 智能 | 图片理解（视觉模型） | M4 | ✅ |
| 9 | 执行引擎 | 插件化执行引擎框架（BotExecutor ABC + Registry） | M1 | ✅ |
| 10 | 执行引擎 | 热加载机制（importlib + POST /api/plugins/reload） | M1 | ✅ |
| 11 | 执行引擎 | 执行引擎 UI 配置（Bot 编辑页切换插件） | M1 | ✅ |
| 12 | 执行引擎 | simple_v1 插件（单次 AI 调用 + 流式） | M1 | ✅ |
| 13 | 执行引擎 | tool_loop_v1 插件（多轮工具调用循环，≤10 轮） | M1 | ✅ |
| 14 | 执行引擎 | 死循环保护（连续 5 次纯工具调用强制 break） | P1 | ✅ |
| 15 | 执行引擎 | react_v1 插件（Thought → Action → Observation） | M4 | ✅ |
| 16 | 记忆 | Bot 个人经验记忆（Chroma + 摘要，跨群组携带） | M0 | ✅ |
| 17 | 记忆 | MEMORY.md 长期手写记忆（startup 注入，write 保护） | M2 | ✅ |
| 18 | 知识 | 项目知识库集成（双轨检索：项目 KB + 个人记忆） | M4 | ⬜ |
| 19 | 工作区 | Bot 私有工作区文件系统（IDENTITY / SOUL / BOOTSTRAP / skills / logs） | M1 | ✅ |
| 20 | 工作区 | Skill 发现 + frontmatter 解析（name / description / always） | M1 | ✅ |
| 21 | 工作区 | Skill always 常驻注入（全文注入 system prompt） | M1 | ✅ |
| 22 | 工作区 | 日志写回（logs/YYYY-MM-DD.md，工具统计 + 迭代轮数） | M2 | ✅ |
| 23 | 工作区 | 群组共享工作区初始化（group_{id}/shared/） | M2 | ✅ |
| 24 | 工作区 | BOARD.md 任务看板（多 Bot 协作状态 source of truth） | M2 | ✅ |
| 25 | 工作区 | deliverables/ 交付物目录 | M2 | ✅ |
| 26 | 工作区 | Skill 注入格式升级（`<available_skills>` XML） | M2 | ✅ |
| 27 | 工作区 | Skill `when_to_use` 字段（调用时机提示） | M2 | ✅ |
| 28 | 工作区 | L1 General Skills（system/skills/，5 个内置通用技能） | M2 | ✅ |
| 29 | 工作区 | L2 Group Skills（group_{id}/shared/skills/，领域通用） | M2 | ✅ |
| 30 | 工作区 | L3 Role Skills（roles/{role}/skills/，角色专属） | M2 | ✅ |
| 31 | 工作区 | L1/L2/L3 运行时注入修复（list_skills_all + _skills_dir_for_layer） | P1 | ✅ |
| 32 | 工作区 | L4 Learned Skills 草稿审批（write → draft/ → 用户审批 → active/） | M3 | ✅ |
| 33 | 工作区 | `learns: true` frontmatter（执行后自动触发写回总结） | M3 | ✅ |
| 34 | 工作区 | Skill 生命周期模块重构（backend/skills/ 独立包） | M3 | ✅ |
| 35 | 工作区 | `status` / `layer` frontmatter 字段 | M2 | ✅ |
| 36 | 工作区 | list_skills() 四层合并 + 状态返回（L1→L2→L3→L4→personal） | M2 | ✅ |
| 37 | 工作区 | Session 注入事件广播（skills_loaded，标记注入状态） | M2 | ✅ |
| 38 | 工作区 | Skill 状态面板 UI（SkillPanel.jsx，层级 / toggle / 审批） | M2 | ✅ |
| 39 | 工作区 | Skill 热更新（watchdog + 300ms debounce + skills_changed WS） | M3 | ✅ |
| 40 | 工作区 | Skill token 预算控制（1% context window，单条 250 char 截断） | M3 | ✅ |
| 41 | 工作区 | Skill Filter 上下文过滤（when_to_use 关键词匹配） | M3 | ✅ |
| 42 | 工作区 | Skill 路径条件激活（`paths:` glob frontmatter） | M3 | ✅ |
| 43 | 工作区 | Skill Fork 子 Agent（`context: fork`，独立 AI 调用） | M3 | ✅ |
| 44 | 工作区 | Skill 目录结构 + 脚本执行（SKILL.md + `` ```! `` / `` !`cmd` `` 嵌入） | M3 | ✅ |
| 45 | 工作区 | 工作流执行日志归档（group_{id}/runs/ 每次有工具调用写档） | M3 | ✅ |
| 46 | 工具 | Bot 工具调用 / Function Calling（tool_executor 注册表） | M1 | ✅ |
| 47 | 工具 | run_shell / read_local_file / write_local_file（跨平台） | M1 | ✅ |
| 48 | 工具 | run_shell `background` 参数（后台启动，返回 PID） | M1 | ✅ |
| 49 | 工具 | Skill `max_iterations` frontmatter（动态扩展执行上限） | M1 | ✅ |
| 50 | 工具 | beforeToolCall 安全钩子（危险命令黑名单拦截） | M1 | ✅ |
| 51 | 工具 | 敏感路径兜底保护（~/.ssh / .env / *.pem 等，路径穿越防护） | P1 | ✅ |
| 52 | 工具 | afterToolCall 结果钩子（工具结果超 20K → head+tail 各 10K 截断） | M3 | ✅ |
| 53 | 工具 | 代码执行沙箱（Skill 目录结构 + run_shell，跨平台） | M3 | ✅ |
| 54 | 工具 | Skill `allowed-tools` 白名单（限制 skill 可调用工具范围） | M3 | ✅ |
| 55 | 工具 | Skill `model` frontmatter（skill 执行时覆盖模型选择） | M4 | ✅ |
| 56 | 工具 | 工具并发执行（只读工具 asyncio.gather，写入工具串行） | P2 | ✅ |
| 57 | 工具 | Hook 条件过滤（`if:` 正则匹配工具名+参数，不匹配跳过 hook） | P2 | ✅ |
| 58 | 工具 | 代码执行沙箱（容器隔离，Docker 每次起/销毁） | M4 | ⬜ |
| 59 | 压缩 | 工具结果 Head+Tail 截断（20K，各 10K） | P1 | ✅ |
| 60 | 压缩 | 跨 run 历史压缩（pre-run Strategy 1 + compact_conversation） | P1 | ✅ |
| 61 | 压缩 | API 溢出恢复（AIContextOverflowError + 压缩 + 重试） | P1 | ✅ |
| 62 | 压缩 | 自适应 Token 阈值（Claude Code 公式：window - 20K - 13K） | P2 | ✅ |
| 63 | 压缩 | 结构化压缩摘要模板（9 段 + \<analysis\> 草稿区） | P2 | ✅ |
| 64 | 压缩 | DB 历史软删除归档（maybe_compact_db_history，post-run 异步） | P2 | ✅ |
| 65 | 压缩 | Strategy 1 计数式微压缩（保留最近 5 个工具结果） | P2 | ✅ |
| 66 | 压缩 | Strategy 2 Snip（70% 窗口阈值，保留最近 4 对对话） | P2 | ✅ |
| 67 | 压缩 | Strategy 3 Session Memory（增量摘要复用已有摘要） | P2 | ✅ |
| 68 | 压缩 | Strategy 4 AI 全量摘要（9 段结构化 + format_compact_summary） | P2 | ✅ |
| 69 | 压缩 | Strategy 5 Cached Microcompact（Anthropic context_management，Claude only） | P2 | ✅ |
| 70 | 压缩 | Token 估算精度（json.dumps 序列化长度 // 4） | P3 | ✅ |
| 71 | 压缩 | 文件操作跨压缩跟踪（_file_tracker + build_file_tracker_xml XML） | P3 | ✅ |
| 72 | 压缩 | 压缩后文件重注入（build_file_contents_for_reinject，modified 优先） | P3 | ✅ |
| 73 | 权限 | 基础规则模型（Rule = {permission, pattern, action}，三态 allow/ask/deny） | P3 | ✅ |
| 74 | 权限 | 决策 Pipeline（敏感路径兜底 → deny → allow → ask，ask 挂起等回复） | P3 | ✅ |
| 75 | 权限 | 规则持久化（always → SQLite；once → 内存；前端 approve/deny UI） | P3 | ✅ |
| 76 | 权限 | 全局权限模式（default / bypassPermissions / dontAsk 三档） | P3 | ✅ |
| 77 | 权限 | Subagent 权限继承（子 Agent 权限 ⊆ 父 Agent，spawn 时裁剪） | P3 | ✅ |
| 78 | 用户体验 | 用户 Abort（WebSocket abort → asyncio.Task.cancel()） | P2 | ✅ |
| 79 | 协作 | 自定义工作流（关键词驱动角色链，done_keyword 推进） | M0 | ✅ |
| 80 | 协作 | 并行任务池（TICKETS 格式，多 Bot 队列认领） | M0 | ✅ |
| 81 | 协作 | @all 顺序协作（同角色竞速，不同角色顺序执行） | M0 | ✅ |
| 82 | 协作 | Before-finalize 质量钩子（审查 Bot APPROVED/REJECTED，可重试） | M3 | ✅ |
| 83 | 协作 | steer() 中途打断（运行中注入新指令，工具轮边界消费） | M3 | ✅ |
| 84 | 协作 | followUp() 后续消息队列（run 结束后剩余 steer → 独立新 run） | M3 | ✅ |
| 85 | 协作 | 子 Agent 派生（spawn_agent，同步等待，spawn_depth 防递归） | M3 | ✅ |
| 86 | 协作 | 定时任务（cron / heartbeat，Bot 周期性自主执行） | M4 | ✅ |
| 87 | 协作 | 后台 spawn_agent（background: true，父 Agent 不阻塞，结果 steer 注回） | P4 | ✅ |
| 88 | 协作 | once / asyncRewake Hook（一次性 hook；exit code=2 唤醒主模型） | P4 | ✅ |
| 89 | 协作 | 子 Agent 任务恢复（session_id 复用已有 context，续传不重建） | P4 | ⬜ |
| 90 | 平台 | 可视化工作流编排（n8n 风格拖拽） | M4 | ⬜ |
| 91 | 平台 | Azure OpenAI 企业认证（Device Code Flow + token refresh） | M4 | ⬜ |

> ✅ 已完成：83 项　　⬜ 未做：8 项　　合计：91 项

---

## 核心架构原则

Bot 是多维度智能体，每个维度独立演进。**执行引擎（Executor）** 是 Bot 的运行时大脑，决定它如何推理、调用工具、使用记忆——通过插件化实现，随时可以热加载替换，无需重启服务。

```
Bot = 身份 × 人格 × 智能 × 记忆 × 知识 × 工作区 × 工具 × 协作
                        ↑
              执行引擎插件决定如何驱动上述所有能力
```

---

## 功能全景（按能力维度）

### 一、身份层 Identity

| 功能 | 里程碑 | 状态 | 说明 |
|---|---|---|---|
| 角色模板库（一键添加） | M0 | ✅ 已完成 | 预置 10 个常用角色 |
| Bot 编辑（提示词 / 模型） | M0 | ✅ 已完成 | hover 显示铅笔图标 |

---

### 二、人格层 Personality

| 功能 | 里程碑 | 状态 | 说明 |
|---|---|---|---|
| Bot 人格配置（temperature / max_tokens） | M0 | ✅ 已完成 | 滑块 + 数字输入，运行时注入 |
| Bot 性格系统（维度滑块 → 指令集） | M0 | ✅ 已完成 | 5 维度生成行为指令，可二次编辑 |

---

### 三、智能层 Intelligence

| 功能 | 里程碑 | 状态 | 说明 |
|---|---|---|---|
| 流式输出（打字机效果） | M0 | ✅ 已完成 | SSE + WebSocket 逐 token 推送 |
| 多模型支持（OpenAI / Ollama / Claude） | M0 | ✅ 已完成 | 后端多 provider，前端可选模型 |
| API 限流重试 + fallback_model | P1 | ✅ 已完成 | `AIRateLimitError(wait_seconds)` 子类；`_parse_retry_after` 解析 `retry-after-ms` / `retry-after` header（大小写不敏感）；指数退避 `max(server_hint, 2^attempt)`，最多 3 次重试；`call_ai_once` 新增 `fallback_model` 参数，主模型耗尽重试后自动切换备用模型 |
| 图片理解 | M4 | ✅ 已完成 | `file_url`/`file_type` 经 `dispatch_bots` → `ExecutionContext` 流入各执行器；`build_image_content` 按 provider 构造 OpenAI image_url 格式或文本降级；`_to_claude_messages` 将 image_url 块转 Claude source.url 格式；DeepSeek/Ollama 降级为 `[附图：URL]` 纯文本 |

---

### 四、执行引擎层 Executor Plugin System ⭐

Bot 的推理循环、工具调用方式、记忆使用策略均由执行引擎插件定义。插件自带工具定义与能力清单（manifest），支持热加载，换完立即生效。

| 功能 | 里程碑 | 状态 | 说明 |
|---|---|---|---|
| 插件化执行引擎框架 | M1 | ✅ 已完成 | BotExecutor ABC + PluginManifest + ExecutionContext + Registry |
| 热加载机制 | M1 | ✅ 已完成 | importlib 重加载，`POST /api/plugins/reload` 触发 |
| 执行引擎 UI 配置 | M1 | ✅ 已完成 | Bot 编辑页面展示可用插件及其 manifest，直接切换 |
| `simple_v1` 插件 | M1 | ✅ 已完成 | 现有逻辑提取：单次 AI 调用 + 流式输出 + 自动记忆积累 |
| `tool_loop_v1` 插件 | M1 | ✅ 已完成 | 多轮工具调用循环（≤10 轮）：AI → 工具请求 → 结果注入 → 继续，直至完成 |
| Context Compaction（上下文自动压缩） | M1 | ✅ 已完成 | AutoCompact 5 策略（参考 Claude Code）：Strategy 1 计数式微压缩 / Strategy 2 Snip / Strategy 3 Session Memory 增量摘要 / Strategy 4 九段结构化 AI 全量摘要 / Strategy 5 Cached Microcompact（Claude only）；电路熔断器；DB 软删除归档；broadcast strategy 字段 |
| 死循环保护（Doom Loop） | P1 | ✅ 已完成 | `_DOOM_LOOP_THRESHOLD = 5`；`_consecutive_tool_only` 计数器在工具循环内逐轮递增，AI 返回文本时归零；连续 5 次纯工具调用后强制 break，回复 `[循环保护] 连续 N 次工具调用，已终止循环` |
| `react_v1` 插件 | M4 | ✅ 已完成 | ReAct 推理循环：Thought → Action → Observation；自由文本解析（无需 provider 函数调用）；`react_thought / react_action / react_observation` WS 事件；最多 20 轮；重复 Action 保护 |

**插件 manifest 示例：**
```
simple_v1       无工具 / 单轮 / 短期+向量记忆
tool_loop_v1    文件读写+代码执行 / 多轮(≤10) / 全记忆层 / 工作区读写
react_v1        全工具 / 无限循环 / 全记忆层 / 子 Agent 派生
```

---

### 五、记忆层 Memory

| 功能 | 里程碑 | 状态 | 说明 |
|---|---|---|---|
| Bot 个人经验记忆（自动积累，按 bot_id） | M0 | ✅ 已完成 | 对话中自动积累，Chroma + 摘要，跟着 Bot 走，跨群组跨项目携带 |
| MEMORY.md 长期记忆 | M2 | ✅ 已完成 | Bot 创建时自动生成模板（能力图谱 / 项目经历 / 重要决策 / 备注）；加入 startup_files 每次会话注入；`write_file` 写保护，Bot 无法覆盖，用户通过工作区 UI 编辑 |

---

### 六、知识层 Knowledge

| 功能 | 里程碑 | 状态 | 说明 |
|---|---|---|---|
| 项目知识库集成 | M4 | ⬜ 未做 | Bot 创建时绑定项目知识来源，对话时双轨检索（项目 KB + 个人记忆） |

---

### 七、工作区层 Workspace

Bot 的文件系统身份，文件即身份，数据库只存索引。工作区分三层，权限和生命周期各不相同。

**工作区目录结构：**
```
workspaces/
├── system/                          # 系统层：平台内置，所有 Bot 只读
│   └── skills/                      # L1 General Skills
│       ├── read-file.md
│       ├── create-skill.md
│       └── search-code.md
│
├── roles/                           # 角色层：角色专属技能库
│   ├── developer/skills/            # L3 Role Skills（Developer）
│   │   ├── code-review.md
│   │   ├── write-unit-test.md
│   │   └── commit-and-pr.md
│   └── pm/skills/                   # L3 Role Skills（PM）
│       ├── write-spec.md
│       └── update-board.md
│
├── bot_{id}/                        # 私有层：Bot 个人，只有自己能读写
│   ├── IDENTITY.md                  # 角色定义，startup 注入 system prompt
│   ├── SOUL.md                      # 价值观 / 行事原则
│   ├── BOOTSTRAP.md                 # 每次会话开始时执行的初始化指令
│   ├── MEMORY.md                    # 长期手写记忆，永不覆盖（M2）
│   ├── skills/
│   │   ├── code_review.md           # .md 技能：作为提示词返回
│   │   ├── deploy.py                # .py 技能：代码沙箱执行（M3）
│   │   └── learned/                 # L4 自学技能（两段式审批）
│   │       ├── draft/               # Bot 写入区，不自动生效
│   │       │   └── project-conventions.md
│   │       └── active/              # 用户审批后移入，才注入 prompt
│   │           └── team-preferences.md
│   └── logs/
│       └── YYYY-MM-DD.md            # 每日执行日志
│
└── group_{id}/                      # 共享层：群组所有成员可读写
    └── shared/
        ├── BOARD.md                 # ⭐ 任务看板
        ├── SPEC.md                  # PM Bot 写的需求文档
        ├── API_CONTRACT.md          # 架构 Bot 定的接口约定
        ├── skills/                  # L2 Group Skills（领域通用技能）
        │   ├── dev-setup.md         # 这个群组/项目的环境搭建方式
        │   ├── run-tests.md         # 项目测试命令
        │   └── deploy.md            # 发布流程
        └── deliverables/            # 各 Bot 提交的交付产出
            ├── user_service.py
            └── order_service.py
```

> **group_id 对应页面上的群组**。在页面创建「电商项目」群组时，`group_id` 确定，`workspaces/group_{id}/` 路径同步创建。

**BOARD.md — 群组任务看板设计：**
```markdown
# 工作看板 · 电商项目

更新时间：2026-05-24 14:32

## Backlog
| # | 需求 | 优先级 |
|---|------|--------|
| #003 | 权限管理模块 | P1 |
| #004 | 导出 Excel | P2 |

## 进行中
| # | 需求 | 负责人 | 状态 | Todo |
|---|------|--------|------|------|
| #001 | 用户登录 | Dev A | 🔨 开发中 | ☑ schema设计 ☐ JWT实现 ☐ 单测 |
| #002 | 订单接口 | Dev B | 🧪 联调中 | ☑ 接口定义 ☑ 业务逻辑 ☐ 错误处理 |

## 已完成
| # | 需求 | 负责人 | 完成时间 | 产出 |
|---|------|--------|---------|------|
| #000 | 数据库初始化 | Dev A | 2026-05-24 10:00 | deliverables/schema.sql |
```

**多 Bot 协作时 BOARD.md 的使用流程：**
```
架构 Bot  → 初始化 BOARD.md，把需求拆成 ticket 写入 Backlog
Dev A/B   → 读 BOARD.md 认领优先级最高的 ticket → 更新状态为「🔨 开发中」
           → 干活过程中随时勾 Todo → 完成后移到「已完成」，填写 deliverables 路径
QA Bot    → 读 BOARD.md 找「已完成」→ 读 deliverables/ 验收 → 更新状态「✅ 验收通过」
PM / 用户 → 任何时候读 BOARD.md，全局进度一目了然
```

> 状态在文件里，不依赖聊天消息传递。Bot 重启、用户下线，进度不丢失。

| 功能 | 里程碑 | 状态 | 说明 |
|---|---|---|---|
| Bot 私有工作区文件系统 | M1 | ✅ 已完成 | `IDENTITY / SOUL / BOOTSTRAP / MEMORY.md / skills/ / logs/`，read/write/list/run_skill 工具已注册 |
| Skill 发现 + frontmatter 解析 | M1 | ✅ 已完成 | `skills/` 目录自动扫描；解析 `name / description / always` frontmatter 字段 |
| Skill `always` 常驻注入 | M1 | ✅ 已完成 | `always: true` 的 skill 全文注入 system prompt，其余懒加载元数据 |
| 日志写回 | M2 | ✅ 已完成 | 每次执行结束写入 `logs/YYYY-MM-DD.md`；记录用户消息、工具调用统计（name ×n）、迭代轮数、回复摘要；`simple_v1` / `tool_loop_v1` 均已接入 |
| MEMORY.md 长期记忆 | M2 | ✅ 已完成 | 同工作区层条目 |
| 群组共享工作区初始化 | M2 | ✅ 已完成 | 群组创建时自动建 `group_{id}/shared/`，生成 BOARD.md / SPEC.md / deliverables/ |
| BOARD.md 任务看板 | M2 | ✅ 已完成 | 群组创建时自动生成，Bot 通过 write_file 工具自主维护状态 |
| deliverables/ 交付物目录 | M2 | ✅ 已完成 | 群组共享工作区初始化时自动创建 |
| Skill 注入格式升级（XML） | M2 | ✅ 已完成 | `<available_skills>` XML 块注入，支持 `when_to_use` 字段 |
| Skill `when_to_use` 字段 | M2 | ✅ 已完成 | frontmatter 解析 + XML 注入时带入 |
| **四层 Skill 架构** | | | |
| L1 General Skills（系统级通用技能） | M2 | ✅ 已完成 | `system/skills/` 5 个内置技能：read-file / write-file / search-code / create-skill / run-tests；`layer: system` 只读 |
| L2 Group Skills（群组领域技能） | M2 | ✅ 已完成 | `group_{id}/shared/skills/` 目录，群组创建时自动初始化（空目录，用户 / Bot 可写入领域技能） |
| L3 Role Skills（角色专属技能） | M2 | ✅ 已完成 | `roles/{role}/skills/` 预置技能：developer(4) / pm(2) / qa(2)；跟角色走不跟 Bot 走 |
| L1/L2/L3 运行时注入修复 | P1 | ✅ 已完成 | `tool_loop_v1` 原使用 `list_skills(bot_id)`（仅 personal 层），改为 `list_skills_all(bot_id, group_id, role)`；`load_always_skills` 同步升级，新增 `_skills_dir_for_layer()` 按层解析路径，L1/L2/L3 always-skill 可正确加载全文 |
| L4 Learned Skills 草稿审批机制 | M3 | ✅ 已完成 | `write_file` 拦截 `learned/active/` 写入并重定向到 `draft/`；bot 直接写 `draft/` 也支持；写入后广播 `skill_draft_added` 事件；system prompt 内置写法指令；`approve/reject` API + SkillPanel UI 完整 |
| `learns: true` frontmatter | M3 | ✅ 已完成 | `_parse_frontmatter` 解析 `learns` 字段；`run_skill` 执行后在 `ctx` 设 `skill_learns` 标记；`tool_loop_v1` 检测到标记后向 messages 注入提示，要求 bot 把执行总结 write_file 到 `learned/draft/` |
| **Skill 生命周期模块重构** | M3 | ✅ 已完成 | 所有 skill 逻辑从 `workspace.py` 抽取到独立 `backend/skills/` 包：`constants`（路径常量）/ `metadata`（frontmatter 解析）/ `discovery`（四层扫描）/ `lifecycle`（状态管理 + 草稿审批）/ `loader`（加载 + 执行）；`workspace.py` 只保留文件 I/O；`workspace_routes.py` 和 `tool_loop_v1.py` 改用 `from skills import …` |
| **Skill 状态可视化** | | | |
| `status` / `layer` frontmatter 字段 | M2 | ✅ 已完成 | frontmatter 新增 `status: active/disabled/deprecated` 和 `layer: system/group/role/personal/learned`；`list_skills()` 解析并过滤 disabled |
| `list_skills()` 四层合并 + 状态返回 | M2 | ✅ 已完成 | `list_skills_all()` 按 L1→L2→L3→L4→personal 扫描，同名后层覆盖前层，每条记录带 `layer / status / injected` 字段 |
| Session 注入事件广播 | M2 | ✅ 已完成 | `tool_loop_v1` 执行时广播 `skills_loaded` 事件，标记哪些 skill 已注入（全文 / 元数据 / 未注入） |
| Skill 状态面板 UI | M2 | ✅ 已完成 | `SkillPanel.jsx`：展示层级 badge / status / 注入状态；toggle enable/disable；L4 draft 审批（通过/拒绝）；WorkspacePanel 入口集成 |
| Skill 热更新 | M3 | ✅ 已完成 | `watchdog` 监听 `workspaces/` 目录（递归），300ms debounce；解析路径区分 bot/group/system/role 来源；广播 `skills_changed` WS 事件；前端 `SkillPanel` 监听 `CustomEvent` 自动重新拉取，无需刷新页面 |
| Skill token 预算控制 | M3 | ✅ 已完成 | 单条 description 截断至 250 chars；metadata XML 总预算 = max(3000, 1% × context_window × 4)；deepseek 3000 / claude 8000；超出预算的 skill 跳过并在 XML 末尾添加注释，`skills_snapshot` 中标记 `injected: null`；`model_name` 提前到 run() 顶部以供 budget 计算使用 |
| Skill Filter 上下文过滤 | M3 | ✅ 已完成 | `skills/filter.py`：无 `when_to_use` 的 skill 始终注入；有 `when_to_use` 的 skill 提取关键词（中文二字 bigram + 英文 3+ char）与用户消息匹配，不匹配则过滤；在 token 预算之前执行；`skills_snapshot` 中过滤掉的 skill 标记 `injected: null` |
| Skill 路径条件激活（`paths:`） | M3 | ✅ 已完成 | `metadata.py` 解析 `paths:` 字段（逗号分隔 glob）；`filter.py` 从用户消息提取文件路径，用自实现 glob 编译器（支持 `**`）匹配；`paths:` 匹配则强制注入（忽略 `when_to_use`）；`paths:` 定义但不匹配则强制排除 |
| Skill Fork 子 Agent（`context: fork`） | M3 | ✅ 已完成 | `metadata.py` 解析 `context: fork` 字段；`loader.py` 检测到 fork context 时设 `ctx["skill_fork"]` 并返回 `__SKILL_FORK__` sentinel；`tool_loop_v1.py` 新增 `_run_fork_skill(skill_content, task, provider, model, temperature)` 函数，以 skill 内容为 system prompt、args 或 user_message 为 user turn 发起独立 AI 调用（无工具）；广播 `skill_fork_start` / `skill_fork_end` WS 事件 |
| Skill 目录结构 + 脚本执行支持 | M3 | ✅ 已完成 | 对齐 claude-code 设计，`loader.py` 完整处理管道：① 注入 `Base directory` 头；② `$ARGUMENTS` / `$ARGUMENTS[N]` / `$N` 参数替换；③ `${SKILL_DIR}` 路径变量；④ `` ```! `` 代码块和 `` !`cmd` `` 内联两种语法在 prompt 发出前执行 shell 命令并替换为输出（bash 默认，`shell: powershell` frontmatter 切换到 PowerShell）；⑤ 目录 skill（SKILL.md）自动附加 `<skill_files>` 伴随文件列表，AI 用 `run_shell` 执行；flat `.py` stub 改为提示路径；fork 模式拿到的也是完整处理后的 content |
| 工作流执行日志归档 | M3 | ✅ 已完成 | `workspaces/group_{id}/runs/YYYY-MM-DD_HHMMSS_{run_id[:8]}.md`，每次有工具调用的执行写一个文件；记录：Bot 信息、用户消息、每次工具调用（name/args/result 前 500 字符）、迭代轮数、最终回复前 2000 字符；`workspace.archive_run()` 异步写入不阻塞主流程；`init_group_workspace` 自动建 `runs/` 目录 |

#### 四层 Skill 架构设计

Bot 在执行时按 L1 → L2 → L3 → L4 顺序加载技能，后层同名 skill 覆盖前层。

| 层级 | 路径 | 归属 | 内容定位 | 写入方 |
|---|---|---|---|---|
| **L1 General** | `workspaces/system/skills/` | 系统级，全局只读 | 通用能力：文件读写、创建 skill、搜索代码 | 平台内置 |
| **L2 Group** | `group_{id}/shared/skills/` | 群组领域，群组内共享 | 领域通用流程：环境搭建、测试命令、发布规范 | 用户 / Bot 写入 |
| **L3 Role** | `workspaces/roles/{role}/skills/` | 角色专属，跟角色走不跟 Bot 走 | 角色能力：Developer → code-review；PM → write-spec | Bot 创建时按 role 自动生成 |
| **L4 Learned** | `bot_{id}/skills/learned/active/` | Bot 个人沉淀，可控扩展 | Bot 自学的项目规律、团队偏好 | **Bot 写 draft/，用户审批后移入 active/** |

**禁用上层 Skill（Override Stub 机制）：**

L1/L2/L3 的 skill 文件不在 Bot 私有目录里，无法直接改写。禁用时在 `bot_{id}/skills/` 创建同名 stub 文件，靠 personal 层压过上层：

```markdown
---
name: read-file
layer: personal
status: disabled
---
```

重新启用时，检测到 stub 内容（frontmatter 仅含 name/layer/status，body 为空），自动删除文件，上层定义恢复生效。Bot 自己写的完整 personal skill 不受此逻辑影响，走正常 frontmatter 改写。

**L4 两段式审批机制（防止失控扩展）：**
```
Bot 发现可复用规律
  → write_file 只能写入 learned/draft/（不自动生效）
  → 前端显示「待审批技能」通知
  → 用户确认 → 移入 learned/active/（开始注入 prompt）
  → 用户拒绝 → draft 文件删除
```
设计参考：Claude Code CLAUDE.md（AI 建议写什么，人决定是否写入）+ openclaw 受控记忆激活模式。

**触发条件（Bot 何时写 draft）：**
- 用户显式说「记住这个做法」/ 「把这个加入你的技能」
- Skill 文件 frontmatter 声明 `learns: true`，执行后允许写回总结

#### Skill 系统设计备忘（参考实现对比）

对比研究了 OpenCode、OpenClaw、Claude Code 三个参考实现，关键设计决策记录如下：

**Frontmatter 字段演进路线：**

| 字段 | 里程碑 | 说明 |
|------|--------|------|
| `name` | M1 ✅ | skill 显示名 |
| `description` | M1 ✅ | 注入 skill 列表时使用，替代读第一行 |
| `always` | M1 ✅ | `true` 则全文常驻 system prompt，否则懒加载元数据 |
| `when_to_use` | M2 | 帮助模型判断调用时机，注入到 skill 列表条目中 |
| `user-invocable` | M2 | ✅ 已完成 | `false` 则不出现在 skill 列表，只能 `always` 方式激活 |
| `argument-hint` | M3 | ✅ 已完成 | 调用参数提示，注入到 skill 列表 XML `<argument_hint>` |
| `paths` | M3 | ✅ 已完成 | gitignore 语法，文件路径匹配时自动激活（参考 Claude Code） |
| `context` | M3 | ✅ 已完成 | `inline`（默认）/ `fork`（独立子 Agent 执行） |
| `allowed-tools` | M3 | ✅ 已完成 | skill 执行时允许使用的工具白名单；inline 模式写入 `ctx["skill_allowed_tools"]`，fork 模式携带在 skill_fork dict 并过滤 tool_schemas |
| `model` | M4 | ✅ 已完成 | skill 执行时覆盖模型选择；inline skill 写入 `ctx["skill_model"]`，下轮 AI 调用使用该模型；fork skill 携带在 `skill_fork` dict，`_run_fork_skill` 直接使用；不写则透明，保持 Bot 默认模型 |

**注入格式升级方案（目标 M2）：**

当前（纯文本）：
```
【可用技能】
  - code_review: Code Review 技能
  - deploy: 部署检查清单
使用 run_skill(name="技能名") 调用
```

目标（XML，参考 OpenCode / OpenClaw）：
```xml
<available_skills>
  <skill>
    <name>code_review</name>
    <description>Code Review 技能</description>
    <when_to_use>当用户要求审查代码时调用</when_to_use>
  </skill>
  <skill>
    <name>deploy</name>
    <description>部署检查清单</description>
  </skill>
</available_skills>
使用 run_skill(name="技能名") 调用
```

**三个参考实现的关键差异：**

| 特性 | OpenCode | OpenClaw | Claude Code |
|------|---------|---------|------------|
| 注入位置 | System Prompt（XML） | System Prompt（XML） | Attachment 消息（每轮动态） |
| Token 预算控制 | ❌ 无限制 | ❌ 无限制 | ✅ 1% context window，单条 250 char |
| 热更新 | ❌ | ✅ chokidar | ✅ clearSkillCaches() |
| 路径条件激活 | ❌ | ❌ | ✅ `paths:` frontmatter |
| Fork 子 Agent | ❌ | ❌ | ✅ `context: fork` |
| MCP Skill 支持 | ❌ | ❌ | ✅ 独立 MCP builder |

---

### 八、工具层 Tools

| 功能 | 里程碑 | 状态 | 说明 |
|---|---|---|---|
| Bot 工具调用 / Function Calling | M1 | ✅ 已完成 | tool_executor 注册表 + call_ai_once + OpenAI/Claude 双 provider 适配 |
| run_shell / read_local_file / write_local_file | M1 | ✅ 已完成 | 本地 shell 执行（跨平台）+ 任意路径文件读写，OS 信息注入 system prompt |
| run_shell `background` 参数 | M1 | ✅ 已完成 | `background: true` 后台启动进程（uvicorn 等长驻服务），立即返回 PID 不阻塞 |
| Skill `max_iterations` frontmatter | M1 | ✅ 已完成 | skill 文件可声明 `max_iterations: 25`，调用时动态扩展执行上限，支持长流程任务 |
| beforeToolCall 安全钩子 | M1 | ✅ 已完成 | 工具调用前拦截危险命令，内置黑名单（rm -rf /、mkfs、shutdown 等），可扩展（参考 GSD-2） |
| 敏感路径兜底保护 | P1 | ✅ 已完成 | `_is_sensitive_path()` 防护 `read_local_file` / `write_local_file`；前缀匹配（`~/.ssh` / `~/.aws` / `~/.gnupg` / `~/.config/gcloud` / `~/.kube`，含 `os.sep` 边界防止 `.awslike` 误判）；filename fnmatch（`.env.*` / `*.pem` / `*.key` / `id_rsa*` / `id_ed25519*` / `credentials` / `.netrc` / `*.pfx` / `*.p12`，大小写不敏感）；allowlist（`.env.example/.sample/.template`，大小写不敏感）；`.resolve()` 阻断 `..` 路径穿越与 symlink 绕过 |
| Skill 路径穿越防护（DFT-021） | P1 | ✅ 已完成 | 对齐 gsd-2（`entry.name===expanded` 精确匹配）/ opencode（发现后按 name 查表）设计：`run_skill` 先用 `list_skills()` 发现层结果校验 name，命中才解析路径——模型无法驱动原始文件路径；`skill_path()` 叠加纵深防御：`_is_safe_name()` 拒绝绝对路径 / `/` / `\` / `..` / 空字节，`_contained()` 用 `resolve().is_relative_to(skills_dir)` 阻断越界与 symlink 绕过。同时连带加固 `lifecycle.py` / `api/workspace.py` 的 `skill_path` 调用 |
| run_shell 沙箱档1+档2（DFT-023） | P1 | ✅ 已完成 | 对齐 gsd-2 `exec-sandbox.ts` 的 env 白名单思路。**档1（路径+env 约束，纯 Python）**：`_resolve_shell_cwd()` 把 `cwd` 限制在 `bot_{id}` 工作区内——空 cwd 默认工作区根、相对路径在其下解析、绝对路径或 `..` 越界一律拒绝；`_sandbox_env()` 由全量 `os.environ` 改为白名单（仅 PATH/HOME/USER/SHELL/LANG/LC_*/TERM/TMPDIR/TZ 等），剥离所有 `*_KEY`/`*_TOKEN`/`*_SECRET`/`AWS_*`/`*_PASSWORD` 等密钥，杜绝凭据外泄。**档2（权限管线兜底）**：`run_shell` 经 `_permission_check_hook` 走 ruleset（`tool_loop_v1` 恒构建 ruleset，default 模式下未授权命令挂起→广播 `permission_request` 询问用户）；`_default_shell_guard` 增加 fail-closed：`ruleset is None` 时直接拒绝 run_shell（最高危工具不 fail-open）。**`*_local_file` 收口**：`read_local_file`/`write_local_file` 纳入 `_APPROVAL_REQUIRED_TOOLS`（无 ruleset 时 fail-closed，见 DFT-024），且 `_is_sensitive_path` 黑名单扩展 `.git-credentials`/`.npmrc`/`.pypirc`/`.dockercfg`/`*.keystore`/`*.jks`/`.htpasswd`/`cookies.sqlite` + `~/.docker`/`~/.config/gh`/`~/.config/git`/`~/.password-store` 前缀作纵深兜底。单测 `tests/test_p1_safety.py::TestSensitivePathExtended`（8 例） |
| 统一 DB connect helper：外键+WAL（DFT-028/029） | P1 | ✅ 已完成 | 修复"FK 全程不生效"+"并发 `database is locked`"两个 High 持久化缺陷。`db/__init__.py` 新增 `@asynccontextmanager connect(path=None)`：连接建立后立即 `PRAGMA journal_mode=WAL`（写不阻塞读）+ `PRAGMA busy_timeout=5000`（并发写等待而非立即报错）+ `PRAGMA foreign_keys=ON`（SQLite 默认 OFF，开启后真正强制 FK）。`get_db()` 委托该 helper；`sessions/store.py`(7) / `scheduler/store.py`(6) / `permissions/db.py`(3，保留自身 `chat.db` 路径) / `db/schema.py` init 全部改走 `_db.connect()`，杜绝散落的裸 `aiosqlite.connect`。开启 FK 后顺带修正 `test_sessions.py`/`test_recovery_resume.py` 夹具的悬空引用（插 session 前先 seed `groups`/`members` 父行）。单测 `tests/test_db_pragmas.py`（5 例，含 FK 实际拦截插入）|
| 并发/后台任务链：bg 登记处 + 工作流 abort（DFT-025/026/027） | P1 | ✅ 已完成 | 治理"fire-and-forget task 被 GC / abort 对工作流失效 / 钩子表无锁"三个 High 并发缺陷。**DFT-025**：新增 `core/bg.py` 后台任务登记处（仅依赖标准库，避免与 main/runner/orchestrator 形成 import 环）——`spawn(coro)` 把 task 存入模块级 `_bg_tasks` 强引用集合防止事件循环弱引用提前 GC，并 `add_done_callback` 把未捕获异常落 `logging.error`（旧代码异常只在 GC 时 warning）；runner 的工作流推进、orchestrator 的 `add_to_chroma`/`maybe_summarize`、main 的 `send_auto_reply`/`save_rule` 等散落 `asyncio.create_task` 全部改走它（竞速组 `asyncio.wait` 已持有引用的不动）。**DFT-027**：`spawn_group(group_id, coro)` 在 spawn 之上按群登记，`runner.apply_step` 派发的每个工作流单元都登记到对应群；WS `abort` 改 `bg.abort_group(group_id)` 取消整条链（旧实现 `_running_tasks` 只存最初 dispatch task、且 done_callback 会误删后到消息的登记，workflow 推进游离任务完全漏网）。**DFT-026（复核后缩小范围）**：编排重构后 `PoolStage.observe`/`_advance`/`enter` 等已是同步纯函数，单线程 asyncio 下 stage-dict RMW 与 `active_bot` 写均原子，**不构成真 race**（缺陷描述的 workflow.py 行号已因重构失效）；真正残留的是 `tool_executor` 全局 `_before_hooks`/`_after_hooks` 在并发 `execute()` 下边 `await` 边 `list.remove()`（`ValueError: x not in list` + 漏跑钩子），已改**快照迭代** `for entry in list(...)` + `once` 钩子 **claim-before-fire**（`_claim_once`：移除成功才占用，保证并发下恰好一次且不崩）。单测 `tests/test_bg.py`（7 例：持有/异常落日志/分群 abort 计数/done 清理）+ `tests/test_tool_executor_hooks.py::TestOnceHooks::test_concurrent_execute_with_once_hook_no_valueerror`。**附带修复测试隔离泄漏**：hooks 测试向全局 `tool_executor._defs` 注册同名 `run_shell` ToolDef 却不清理，泄漏到 `test_abort_signal.py` 使其 `tool_schemas` 非空、偏离纯流式分支拿到 `stream_error`——已加 module 级 autouse teardown 在每例后清空 hooks 与 `_defs/_handlers` |
| 连接/资源链：广播写超时 + 共享 httpx 连接池（DFT-030/033） | P1 | ✅ 已完成 | 治理"半开客户端拖垮全局事件投递"+"httpx per-call 无连接池/socket 到 GC 才释放"两个 High 缺陷。**DFT-030（缩小范围）**：`WSManager.broadcast` 每次 `send_json` 改 `asyncio.wait_for(ws.send_json(message), _SEND_TIMEOUT=10s)`——bus adapter 串行经此扇出，半开/慢客户端原先令 `send_json` 永久 await、卡住全 app 事件投递；现在超时即当死连接断开（连带 DFT-009 presence offline），消费者不再被卡即快速 drain wildcard 队列，间接抑制内存无界增长。每连接独立 subscribe + 队列 `maxsize` 的深度重构暂缓（需改 30+ 直接 `manager.broadcast` 调用方 + 连接生命周期，blast radius 大且 WS 集成测试本机会挂），残留「单个病态客户端被剔除前最多 `_SEND_TIMEOUT` 一次性延迟」可接受。**DFT-033**：新增进程级 `_get_client()`（lazy 单例 + `httpx.Limits(max_connections=100, keepalive=20)` + `is_closed` 重建）/ `_shared_client()`（借用不关闭的 CM）/ `aclose_client()`（lifespan 关闭释放）；7 处 `async with httpx.AsyncClient(timeout=N)` 全改走共享池并把 `timeout=N` 下放到各 `.post()/.stream()`，复用 keep-alive 连接消除 per-call TLS 握手。单测 `tests/test_ws_manager.py::TestBroadcastSendTimeout` + `tests/test_ai_client_pool.py`（4 例）+ 既有 `tests/test_ai_client.py` 35 例迁 seam 后全绿。 |
| SQLite 串行写入器消除 database is locked（DFT-053·缩小范围） | P1 | ✅ 已完成 | 治理"用户/Bot 并发上升后频繁消息写入与状态更新触发 `database is locked`"。根因：每次查询各开一条 aiosqlite 连接，而 aiosqlite 每连接跑独立 OS 线程，两个写协程真在两线程并发争 SQLite 单写锁（WAL 也只允一个写者）。新增 `db/writer.py` 进程级**串行写入器**：单条常驻写连接（WAL/busy_timeout/FK pragma）+ 按事件循环 id 存的 `asyncio.Lock`，`write_connect()` CM 持锁 yield 共享写连接——写事务永不重叠，SQLite 不再见到竞争写者；按 loop id 分桶避免单测各自新 loop 复用绑死在已关闭 loop 的锁/连接。`db.__init__` 导出 `write_connect`/`aclose_writer`。已把缺陷点名的消息写入热路径切到串行写入器：`core/orchestration/interaction.StandardInteraction.save_message`（tool_loop_v1 机器人回复主写路径）、`executors/plugins/simple_v1`+`react_v1` 回复落盘、`api/messages` 编辑/撤回、`executors/compact` 历史摘要写入；这些点都是叶子写（广播在锁外，无嵌套写者），切换安全。`main.py` lifespan 关闭新增 `await aclose_writer()` 释放连接。读路径保持 `db.connect()`（WAL 并发读不受影响，未被串行化）。**缩小范围**：`main.py` 入站用户消息保存、`orchestrator` 旧路径、`mark_read`/`send_auto_reply` 等仍各开连接——它们在单个连接块内交织多读 + 嵌套写（`mark_read` 自带 `get_db`+commit），直接套 `write_connect` 会有非重入 `asyncio.Lock` 嵌套死锁风险，安全迁移需先拆解读写边界，留作后续；故 contention 是大幅降低而非完全消除（对标 DFT-030 的诚实缩小范围）。单测 `tests/test_db_writer.py`（4 例：写入读回、两次借用同一连接、并发两写者严格串行且双写落地不报 locked、`aclose_writer` 后重建新连接）；既有 `tests/test_messages.py`/`test_compact.py`/`test_db_pragmas.py` 全绿。 |
| 文件 I/O 不阻塞事件循环（DFT-054） | P1 | ✅ 已完成 | 治理"工作区大文件读写在事件循环线程内同步 `Path.read_text/write_text`，大文件令全 app 的 WS 投递卡顿"。Bot 工具热路径四处读写处理器全部下放工作线程：`workspace.read_file` → `await asyncio.to_thread(p.read_text, encoding="utf-8")`；`workspace.write_file` 把含 learned/active→draft 重定向、draft 直写、history 存档（读旧值+写历史+裁剪）、内容对比、最终落盘的整段磁盘操作收进同步内嵌 `_do_write()` 经 `asyncio.to_thread` 一次性下放（`_safe_path` 校验等纯逻辑仍留循环）；`executors/plugins/workspace_tools.py` 的 `_handle_read_local_file` / `_handle_write_local_file`（含 `mkdir`）同样下放。`append_log`/`archive_run` 此前已用 `asyncio.to_thread`。单测 `tests/test_workspace_async_io.py`（5 例：`_ThreadSpy` 记录 `Path.read_text/write_text` 实际运行线程 id 验证 ≠ 循环线程 + 一例 0.2s 阻塞写并发 ticker 验证循环不停摆），既有 `tests/test_tool_executor_hooks.py` 全绿。 |
| 插件 import 错误不再静默吞 + 健康暴露（DFT-049） | P2 | ✅ 已完成 | 治理"`registry._load_file` 把插件 import 失败只写一行日志即吞掉，全部失败时 `get()` 的 `next(iter(_registry.values()))` 抛 `StopIteration`——整个 bot 系统可零 executor 启动、运行时才暴雷"。`_load_file` catch 分支改为把失败记入模块级 `_failures[path.name]`（含异常类型名）并 `logger.error(..., exc_info=True)` 保留完整 traceback（旧实现只一行 `str(e)`），import 成功时 `_failures.pop` 清该文件条目；`discover()` 在 `_registry.clear()` 旁同步 `_failures.clear()`，避免删文件后陈旧失败残留；新增 `failures()` 访问器返 `dict(_failures)`。健康暴露：新增 `GET /api/plugins/health` 返 `{loaded, failures}`，`POST /api/plugins/reload` 响应补 `failures` 字段；`GET /api/plugins` **保持原数组形态**不破坏前端 `MemberList.jsx`（直接 `setPlugins` 数组并 `.map`）。`get()` 重写：命中 id/simple_v1/任一已注册即返回，**仅当 `_registry` 为空**才抛明确 `RuntimeError`（消息列出 `_failures` 失败明细），不再 `StopIteration`——调用方（runner/orchestrator/recovery/workspace_tools）拿到的是可读错误而非裸迭代器异常。单测 `tests/test_registry_health.py`（3 例：失败被记录而非吞且不进 `_registry`、成功 rediscover 清陈旧失败、空注册表 `get()` 抛 `RuntimeError`）。 |
| 权限路由边界校验（DFT-050·缩小范围） | P2 | ✅ 已完成 | 治理"`permissions/routes.py` 增删改权限规则路由无任何鉴权/属主校验：任何调用方可对任意 `member_id`（含不存在的、或 human 成员）写入 `allow *` 规则绕过权限引擎"。本应用为单机无 auth 体系，全量鉴权/属主体系超出范围；落地**边界校验**斩断滥用向量：新增 `_require_bot_member(member_id)` helper 经 `get_db`+`get_member` 取目标成员——不存在 raise `HTTPException(404)`、`type != 'bot'` raise `403`（权限规则只对 bot 成员有意义，杜绝给不存在/human id 静默写规则）；`GET`/`POST`/`DELETE /api/members/{id}/permissions` 三路由统一前置该守卫。`POST` 另加输入校验：`tool_pattern` 缺失→400、`action` 仅允许 `allow`/`deny`（对齐 `engine.py` 仅识别这两值，挡住任意 action 注入）。单测 `tests/test_permission_routes_authz.py`（6 例：不存在 member 404、human member 403、invalid action 400、bot happy path patch `save_rule` 放行返回 id、GET 不存在 404、DELETE human 403）。**附带修复**：DFT-053 把 `react_v1` 的 `get_db()` 换成 `write_connect()` 后，`test_p1_safety::TestReactV1RulesetWiring::test_execution_ctx_has_ruleset` 仍 patch 已不存在的 `react_v1.get_db` 而 `AttributeError`——同步把 patch 目标改为 `write_connect`，恢复绿灯。 |
| AIError 不再回显原始异常（DFT-051） | P2 | ✅ 已完成 | 治理"`ai/client.py` 多处 `raise AIError(f"AI 调用失败：{str(e)}")` 把底层异常（可能含请求 URL / `Authorization` header / 内网地址）原样塞进 `AIError`，敏感信息泄漏到用户侧"。先确认泄漏路径：消费方 `simple_v1`/`orchestrator`/`tool_loop_v1`/`react_v1` 捕获 `AIError` 后都 `str(e)` 并广播成 `stream_error` 消息直达聊天界面——故 `AIError` 的 message 即用户可见文案。四处兜底 `except Exception as e`（`call_ai` / `call_ai_once` 重试循环 / `call_ai_stream_messages` / `call_ai_stream`）由回显 `str(e)` 改为：新增模块级 `logger = logging.getLogger(__name__)`，`logger.error("AI call failed", exc_info=True)` 把完整 traceback（含 secret）仅写服务端日志，再 `raise AIError("AI 调用失败，请稍后重试") from e`——用户侧只见通用文案；`from e` 保留 `__cause__` 供服务端日志链路追踪但不进 `str(AIError)`（消费方 `str(e)` 仍只拿到通用串）。`httpx.HTTPStatusError` 分支只回显 HTTP `status_code`（数字，非敏感）保持原样。单测 `tests/test_ai_error_no_leak.py`（3 例：向 `call_ai`/`call_ai_stream`/`call_ai_stream_messages` 注入含 `SUPERSECRET` 的异常，断言 `str(AIError)` 不含 secret、含 `AI 调用失败` 通用文案，且 secret 经 `exc_info` 落入日志）；既有 `tests/test_ai_client.py`+`tests/test_ai_client_pool.py` 35 例迁移后全绿。 |
| `estimate_tokens` 去全量 json.dumps（DFT-052） | P3 | ✅ 已完成 | 治理"`executors/compact.py::estimate_tokens` 每次全量 `json.dumps(messages)` 序列化整个消息数组，且每轮 compact 决策调多次——大历史下重复 O(n) 序列化 + 巨型字符串分配 + 逐字符转义的性能损耗"。采用缺陷给定的「长度近似」方案：逐条消息累加 `content` 字符数（新增 `_content_chars()` 助手：`None`→0、`str`→`len()`、其它/多模态 `list`→`len(str(...))` 兜底，避免序列化）+ `name` 字段长度 + `_PER_MESSAGE_OVERHEAD=8`（粗算 role/键名/括号等结构开销），最终仍 `// 4` 维持原 ≈char/4 估算口径，免去每调用一次就为整个数组构造大字符串。`import json` **保留**（不再被执行代码引用，仅供 TDD 守卫测试 patch `compact.json.dumps`）。`tests/test_compact.py` 重写 `TestEstimateTokens`：移除 5 个精确 `json.dumps` 等值断言（实现细节耦合），改为属性断言——随 content 长度单调增长、多消息累加、`list`/`None` content 不崩——并新增 `test_does_not_serialise_whole_array`（patch `compact.json.dumps` 抛 `AssertionError`，断言 `estimate_tokens` 仍返回 `int>400`，证明已不再序列化全数组）；`test_compact.py` 全套 101 例全绿。 |
| 运行指标可观测性 `/api/system/status`（DFT-057） | P3 | ✅ 已完成 | 治理"单机部署缺运行指标，运维无法直观监控活跃任务数与内存队列堆积、及时发现性能瓶颈"。新增 `GET /api/system/status` 聚合三类**内存态**运行指标（路由本身无 DB 查询、无副作用，可随时低成本拉取）：①后台任务——`core/bg.py` 加 `stats()` 只读快照，暴露 `active_tasks`（`_bg_tasks` 强引用集大小）/ `groups_with_active_tasks` / `tasks_by_group`（每群在跑工作流链 task 数，堆积即可见）；②WebSocket 连接——`ws_manager.WSManager` 加 `stats()`，暴露 `groups_online` / `total_connections` / `connections_by_group`；③权限审批队列——`permissions/engine.py` 加 `pending_stats()`（经 `permissions` 包导出），暴露 `pending_requests`（`_pending` ask 队列堆积数，DFT-031 超时清理后应回落）/ `once_grants`（在册一次性放行票数）。三者均为纯读快照，不改任何运行状态。单测 `tests/test_system_status.py`（5 例：bg 活跃/完成任务进出统计、ws 连接计数、权限 pending/once 计数、路由聚合三段 shape 校验经 `httpx.ASGITransport`）；`tests/test_bg.py`+`test_ws_manager.py`+`test_permissions.py`+`test_permission_routes_authz.py` 回归全绿。 |
| DB 迁移失败不再谎报成功（DFT-038） | P2 | ✅ 已完成 | 治理"`db/migrations.py` 多处 `try/except Exception: pass` 既吞良性的 'duplicate column'（幂等重跑），也吞 `database is locked`/磁盘满/`no such table`/语法错误等真实故障，且 `run_migrations` 仍把该 migration 记入 `_schema_version` → schema 半残却谎报成功、永不重试"。新增模块级助手 `_safe_add_column(db, sql)`：`except sqlite3.OperationalError` 时仅当 `"duplicate column" in str(e).lower()` 才静默跳过（写 `log.debug`），其余 `OperationalError` 一律 `raise`。错误从 `migration_fn` 冒泡出 `run_migrations` 的 `await migration_fn(db)`，在其后的 `INSERT INTO _schema_version` 之前中断——**失败的迁移不记版本号**，进程下次启动从同一 version 重试，而非把残缺 schema 标记为已完成。把 7 处吞错块（`migration_001/002/005/006` 的 `for sql in stmts` 循环 + `008/011/012` 的单 `ALTER`）统一替换为该助手；建表类迁移（`CREATE TABLE IF NOT EXISTS`）本就幂等，未受影响。生产流程 `schema.init_db` 始终先建基础表再 `run_migrations`，故 'no such table' 在正常路径不会触发。单测 `tests/test_migrations.py` 新增 `TestMigrationErrorPropagation`（3 例：duplicate 仍被吞、非 duplicate 的 `OperationalError` 上抛、失败迁移后 `_schema_version` 无版本行）；既有 `TestRunMigrations` 3 例原直接对裸空库跑 `run_migrations`（依赖被移除的吞错行为），改为先 `executescript(_OLD_SCHEMA)` 建基础表以对齐真实流程；`test_migrations.py` 全套 28 例 + `test_db_pragmas`/`test_db_writer` 9 例全绿。 |
| AI 记忆失败不再静默吞错（DFT-043） | P2 | ✅ 已完成 | 治理"`ai/memory.py` 的 `maybe_summarize` 整体 + `get_memory_context` 加载摘要段都 `try/except: pass`，记忆压缩/上下文注入一旦失败（DB 错误、`call_ai` 异常等）无任何日志，生产几乎无法排障"。新增模块级 `log = logging.getLogger(__name__)`；两处兜底 `except Exception: pass` 改 `log.exception(...)`（带 `bot_id`/`group_id` 上下文），记录完整 traceback 但**保持不阻断主流程**——记忆是对话增强项而非主链路，失败应可观测而非吞掉。Chroma 语义检索 `_query_sync` 的 `except` 维持返回 `[]`（降级合理）。单测 `tests/test_memory.py` 新增 `TestMemorySilentFailureLogging`（2 例：`patch("ai.memory.get_db", side_effect=RuntimeError)`，断言 `maybe_summarize` 与 `get_memory_context` 均经 `assertLogs("ai.memory", "ERROR")` 记录异常详情、不抛异常，且 `get_memory_context` 仍返回 `str`；后者并 `patch retrieve_relevant` 隔离 chroma 避免触发 embedding 模型下载）；既有摘要生命周期测试不受影响。 |
| 调度器统一 UTC 时区（DFT-040） | P2 | ✅ 已完成 | 治理"`scheduler/engine.py` 的 `AsyncIOScheduler()` 未指定 timezone，用宿主本地时区解释 cron 表达式，而 `cron_jobs.created_at` 存的是 UTC，时区混用 → DST 切换或服务器迁移会整体平移所有定时任务触发时间"。两处一起钉死 UTC：①`start()` 改 `AsyncIOScheduler(timezone="UTC")`；②`_parse_cron` 改 `CronTrigger(..., timezone="UTC")`——关键点：APScheduler 中**预先构造好的 trigger 实例加入调度器后保留它自身的 timezone，并不会继承 scheduler 的 timezone**（scheduler tz 只在用 `trigger='cron'`+字段 kwargs 让调度器现场构造 trigger 时生效）。本项目在 `_parse_cron` 里手工 `new CronTrigger`，若不显式传 tz 则默认取宿主本地时区（实测 `Asia/Shanghai`），故必须在此处显式传 `timezone="UTC"`，仅设 scheduler tz 不够。统一后 cron 字段与 `created_at` 都是 UTC，前端展示时再转本地。单测 `tests/test_scheduler.py` 新增 2 例（`_parse_cron("0 9 * * *")` 的 `trigger.timezone` 为 UTC、`start()` 后 `_scheduler.timezone` 为 UTC）；既有 store/engine/runner/router E2E 共 41 例全绿。 |
| 权限链：ask 超时 + resolve 鉴权 + once 单次消费（DFT-031/032） | P1 | ✅ 已完成 | 治理"权限询问永久挂起 + 跨群越权批准 + once 永久全局放行"。**DFT-031**：`engine.check()` ask 分支由裸 `await future` 改 `asyncio.wait_for(future, _ASK_TIMEOUT_SECONDS=300)`，超时 `TimeoutError`→deny 并 `finally` 清 `_pending`（用户关页面不再让 tool loop 协程永久挂起、`_pending` 不再无界增长）；`_PendingRequest` 加 `group_id`，`resolve(request_id, approved, persistence, group_id=)` 校验 `req.group_id==group_id` 否则返回 None（拿到 `request_id` 的 A 群客户端无法批准 B 群 bot 的工具调用）；新增 `cancel_pending_for_group(group_id)`，`main.py` WebSocketDisconnect 在该群在线数归零时调用，把所有挂起 ask `future.set_result((False,"once"))`——**resolve 成 deny 而非 cancel**，保留 DFT-027 真 abort 的 CancelledError 传播。**DFT-032**：`_once_rules: dict[bot,list[Rule]]` 换成 `_once_grants: dict[(bot_id,group_id), list[(tool_name, args_hash)]]`，"once" 批准只登记一张「该 bot+群+工具+这组参数」的一次性票，`check()` 第 4 步 `_consume_once_grant` 命中即 `remove`（用后即删，不跨群、不永久、参数敏感，`_args_hash` 用 sha256(json sort_keys)）；deny 规则仍先于 once-grant 判定。单测 `tests/test_permissions.py`（24 例：TestOnceGrantSemantics/TestAskTimeout/TestCancelPendingForGroup/TestResolveAuthz）|
| 权限引擎 fail-closed + react_v1 接线（DFT-024） | P1 | ✅ 已完成 | 修复"权限默认 fail-open + ReAct bot 零权限检查"——同一 bot 换 executor 即可绕权限。**fail-closed**：`_permission_check_hook` 在 `ruleset is None` 时对 `_APPROVAL_REQUIRED_TOOLS`（run_shell/write_file/read_local_file/write_local_file/spawn_agent）默认拒绝，只读工具（read_file/list_workspace 等）仍放行，避免误伤。**react_v1 接线**：`react_v1.run` 在 `ctx.ruleset` 为空时按 bot `executor_config.permission_mode`（默认 default）经 `permissions.load_rules` 自建 `Ruleset`，`execution_ctx` 补齐 `ruleset`/`steer_channel`/`rewake_queue`，并在循环顶部 drain rewake 队列（对齐 `tool_loop_v1`），ReAct 工具调用从此与 tool_loop 同权限管线。单测 `tests/test_p1_safety.py::TestPermissionHookFailClosed`（7 例）+ `::TestReactV1RulesetWiring` |
| 崩溃会话恢复·续跑（DFT-018/019） | P1 | ✅ 已完成 | 修复恢复"从头重跑、重复执行已完成副作用工具"+`recovering` 状态永久泄漏两个 Critical 缺陷。**DFT-018**：`ExecutionContext` 增 `resume_session_id`/`resume_messages`；`tool_loop_v1.run` 检测到 resume 时跳过新建 session、跳过群历史重建，剥离前导 system 后直接续跑由 WAL 重建的 messages（含已完成 `tool_result`）；`recovery._dispatch_recovery` 改为专用恢复入口（不再经 `dispatch_bots` 的历史重建），把重建消息经 `ExecutionContext.resume_*` 交给 executor。**DFT-019**：恢复复用同一 `session_id`，executor 正常 completed/failed 回写自然把孤儿迁出 `recovering`（无新行泄漏），异常时 `_dispatch_recovery` 兜底回写 `failed`。单测 `tests/test_recovery_resume.py`（4 例）验证已完成工具不重跑、仅 1 行 session 且最终 `completed` |
| afterToolCall 结果钩子 | M3 | ✅ 已完成 | `tool_executor.add_after_hook(hook)`，签名 `async (name, arguments, result, context) -> str | None`；链式执行，返回新字符串则替换结果，返回 None 保持不变；内置 `_default_output_truncator`：单条工具结果超 20,000 字符时截断为 head(10K)+tail(10K)，中间插 `[... N 字符已省略 ...]`，防止 context window 被大输出塞满 |
| Skill `!` 块执行全禁（DFT-022） | P1 | ✅ 已完成 | 修复"自写 skill = RCE"Critical 链。原 `processor.py` 在加载 skill 文本时把 ```! / !`inline` 块直接 `/bin/sh -c` 执行，**绕过 `tool_executor`**（denylist + 权限管线 + cwd/env 沙箱全失效）；配合 bot 能 `write_file`+`run_skill`，bot 可自写含 `!rm -rf ~` 的 skill 触发任意主机代码执行。**选项 A（全禁）**：删除 `execute_shell_in_prompt`/`_run_shell_cmd`/`!` 块正则与 shell 常量，`process_skill_content` 只保留参数替换 + `${SKILL_DIR}`；`!` 标记作惰性文本透传，不再起任何子进程。skill 需 shell 时由 AI 主动调 `run_shell`（进 `tool_executor` 受全部防护）；`.py` 伴随脚本本就被 `run_skill` 推回 `run_shell`。对标 opencode（skill 正文从不执行 shell）。单测 `tests/test_skill_no_shell_exec.py`（5 例） |
| 代码执行沙箱（subprocess） | M3 | ✅ 已完成（重新定义） | 对齐 claude-code / opencode 设计，不直接执行 `.py`；改为 skill 目录结构（`SKILL.md` + 伴随脚本），AI 通过 `run_shell` 执行目录内脚本；flat `.py` stub 提示路径。无需 subprocess 沙箱，天然跨平台。**注（DFT-022）**：原 `processor.py` 在 prompt 中嵌入并执行 `` ```! ``/`` !`cmd` `` 的能力已全部移除——该路径绕过权限管线构成 RCE，skill 内 shell 一律改走 `run_shell` |
| 代码执行沙箱（容器隔离） | M4 | ⬜ 未做 | 用 Docker 容器执行代码，每次执行起容器、执行完销毁；文件系统挂载限制为 `bot_{id}/workspace/`，网络可选隔离；替换 M3 subprocess 方案 |

---

### 九、协作层 Collaboration

| 功能 | 里程碑 | 状态 | 说明 |
|---|---|---|---|
| 自定义工作流（关键词驱动角色链） | M0 | ✅ 已完成 | done_keyword 推进，顺序 + 并行 |
| 并行任务池（多开发者抢单） | M0 | ✅ 已完成 | TICKETS 格式，队列认领 |
| @all 顺序协作 | M0 | ✅ 已完成 | 同角色竞速，不同角色顺序执行 |
| Before-finalize 质量钩子 | M3 | ✅ 已完成 | 回复前触发审查 Bot，不满意可打回重做；工具循环结束后先用 `call_ai_once` 生成草稿，审查 AI（`reviewer_prompt`）判断 APPROVED/REJECTED，REJECTED 则注入反馈并重新生成，重试上限由 `max_retries` 控制（默认 2）；超出 budget 则 fail-open 使用最后草稿；`reviewer_prompt` 未配置时直接走流式输出，零性能开销；广播 `before_finalize_review / approved / rejected` WS 事件 |
| steer() 中途打断 | M3 | ✅ 已完成 | 用户向正在运行的 Bot 发送新消息时，`dispatch_bots` 检测 `_steer_queues` 注册表，若 Bot 在跑则把新消息放入其专属 `asyncio.Queue`（不起新 run）；`tool_loop_v1` 在每轮工具调用结束后检查 steer queue，将消息以 `[用户中途指令]` 格式追加到 messages，下一次 AI 调用即生效；`main.py` WebSocket 循环改为 `asyncio.create_task(dispatch_bots(...))` 非阻塞，保证 WS 可持续接收消息；广播 `steer_queued` / `steer_injected` WS 事件 |
| followUp() 后续消息队列 | M3 | ✅ 已完成 | Bot run 结束后自动处理在 `steer_q` 中未被注入的消息（即 run 末期才到、没赶上工具边界的消息）；每条 followUp 消息都触发一个独立的新 run（刷新 history、独享新 steer_q、继续支持 steer 注入）；最多 `_MAX_FOLLOWUP_DEPTH=5` 层级联，防无限循环；广播 `followup_start` WS 事件；steer 和 followUp 共用同一 steer_q：mid-run 消费 = steer，run 结束后剩余 = followUp |
| 子 Agent 派生 | M3 | ✅ 已完成 | 新增 `spawn_agent(bot_name, task)` 工具；父 bot 同步等待子 agent 执行完成并拿到结果作为工具返回值；子 agent 用 `_NullBroadcaster` 静默执行（不广播 WS 事件、不存 DB、不写记忆），结果透过 `tool_result` 事件对用户可见；`spawn_depth` 随 `ExecutionContext` 传递，超过 `_SPAWN_MAX_DEPTH=3` 拒绝派生防止递归；`execution_ctx` 新增 `all_bots / all_members / spawn_depth / broadcaster` 字段供 handler 使用；manifest `can_spawn_subagent=True` |
| 定时任务（cron / heartbeat） | M4 | ✅ 已完成 | 独立 `scheduler/` 插件模块（store · engine · runner · router）；APScheduler 3.x AsyncIOScheduler；`migration_003` 新建 `cron_jobs` 表；`runner.py` 为唯一主系统耦合点（lazy import `dispatch_bots`）；REST API 6 个端点含立即触发；删除模块只需改 main.py 3 行 |

---

### 十、平台层 Platform

| 功能 | 里程碑 | 状态 | 说明 |
|---|---|---|---|
| 可视化工作流编排（n8n 风格） | M4 | ⬜ 未做 | 拖拽配置多 Bot 协作流程 |
| Azure OpenAI 企业认证（Device Code Flow） | M4 | ⬜ 未做 | 见下方说明 |

#### Azure OpenAI 企业认证 — 设计备忘

**背景：** 公司内网环境使用 Azure OpenAI，通过 Azure AD 联邦 GitHub 登录完成身份验证（类似 OpenCode 的体验），无法直接拿到静态 API Key。

**认证流程（OAuth Device Code Flow）：**
```
1. 后端请求 Azure AD → 拿到 device_code + user_code + verification_uri
2. 前端弹窗显示 user_code + 跳转链接，提示用户在浏览器输入
3. 后端轮询 Azure AD token 端点，等待用户完成 GitHub 登录
4. 获得 access_token（1小时有效）+ refresh_token
5. 后续 AI 请求携带 Bearer token → 调用公司 Azure OpenAI 端点
6. token 过期前自动 refresh
```

**实现时需要从 IT/管理员拿到：**

| 参数 | 用途 |
|------|------|
| `tenant_id` | Azure AD 租户 ID |
| `client_id` | 应用注册 ID（App Registration） |
| `azure_endpoint` | Azure OpenAI 端点，如 `https://company.openai.azure.com/` |
| `deployment_name` | 部署的模型名，如 `gpt-5.4` |

**代码改动范围：**
- `backend/config.py` — 新增 azure 相关字段
- `backend/ai_client.py` — 新增 `azure` provider，采用 **getApiKey 动态解析模式**（参考 GSD-2）：每次 AI 调用前动态获取最新 token，自动处理过期刷新，无需手动维护 token 生命周期
- `backend/main.py` — 新增 `GET /api/auth/azure/start`、`GET /api/auth/azure/status` 端点
- `frontend/ApiKeyManager.jsx` — 新增「Azure 企业登录」入口 + device code 弹窗 + 状态轮询

**getApiKey 动态解析模式（GSD-2 启发）：**
```python
async def get_api_key(provider: str) -> str:
    if provider == "azure":
        return await azure_token_manager.get_fresh_token()  # 过期自动 refresh
    return config.get_key(provider)  # 其他 provider 读配置
```
AI 调用层不感知 token 生命周期，token 刷新逻辑集中在 `azure_token_manager` 中。

---

## 里程碑规划

| 里程碑 | 主题 | 核心交付 | 依赖 |
|---|---|---|---|
| **M0** 已交付 | 基础 Bot 能力 | 身份 / 人格 / 智能 / 工作流 / 个人记忆 | — |
| **M1** ✅ 已完成 | 引擎 + 工具基础设施 | 执行引擎插件框架 / simple_v1 / tool_loop_v1 / Function Calling / 工作区文件系统 / Skill 发现 + always 注入 | M0 |
| **M2** ✅ 已完成 | 协作工作区 | MEMORY.md / 日志写回 / 群组共享工作区 / BOARD.md 任务看板 / Skill XML 格式 / 四层 Skill 架构 / Skill 状态可视化 | M1（工作区） |
| **M3** 当前 | 质量 + 自主性 | 代码沙箱 / 质量钩子 / 子 Agent / 定时任务 / Skill 热更新 & 路径激活 & Fork & L4 审批 | M1（Function Calling） |
| **M4** 长期 | 平台化 | react_v1 插件 / 可视化工作流编排 / Azure 企业认证 / 项目知识库集成 | M1~M3 |

**关键依赖链：**
```
Function Calling (M1)
  ├── Bot 工作区 (M1)
  │     ├── MEMORY.md (M2)
  │     ├── Skill 发现 (M2)
  │     ├── 群组共享工作区 / BOARD.md (M2)
  │     └── 代码沙箱 (M3)
  ├── Before-finalize 钩子 (M3)
  └── 子 Agent 派生 (M3)

执行引擎框架 (M1)
  ├── simple_v1：单次调用 ✅
  ├── tool_loop_v1：多轮工具调用循环 ✅
  └── react_v1 (M4)
```

---

## 知识架构说明

Bot 的知识来自两个独立来源，运行时同时检索：

| 层级 | 内容 | 来源 | 归属 |
|---|---|---|---|
| **项目知识库** | 项目背景、文档、需求、架构设计 | 创建 Bot 时指定绑定 | 项目级，多 Bot 可共享同一知识库 |
| **Bot 个人记忆** | 参与对话中积累的经验、擅长的方向 | 自动积累（Chroma + 摘要） | Bot 个人，跨群组跨项目携带 |

> Bot 对标真实的人：有自己的能力曲线，擅长某些项目而不擅长另一些项目。  
> 项目知识库提供「了解这个项目背景」，个人记忆提供「我以前做过类似的事」。

---

## Context 管理深度设计备忘

> 基于对 claw-code / opencode / claude-code / gsd-2 四个框架的源码调研（2026-05-27）

### 当前实现 vs 参考框架 Gap 分析

| 问题 | 当前实现 | 参考方案 | 优先级 |
|---|---|---|---|
| 工具结果截断丢尾部 | 超 20K 整体砍头，尾部关键信号（exit code / pass/fail）丢失 | gsd-2: head+tail 各 10K，中间插 `[... N more ...]` | P1 ✅ |
| 跨 run 历史不压缩 | Compaction 只在 tool loop 内触发，两次对话之间历史无限增长 | opencode / gsd-2: post-API 异步检查，每次 run 结束都评估 | P1 ✅ |
| 保留策略粗糙 | 固定保留末尾 6 条消息 | claude-code: effectiveWindow - 20K - 13K 自适应阈值；snip 保留最近 4 对 | P2 ✅ |
| 压缩摘要质量低 | 自由生成摘要，无结构约束 | claude-code: 9 段结构化模板（Primary Request / Key Concepts / Files / Errors / Problem Solving / All User Messages / Pending / Current Work / Next Step）| P2 ✅ |
| 压缩后不恢复文件 | 压缩后关键文件内容丢失 | claude-code: 压缩后重注入最多 5 个关键文件（25K token 预算）| P3 |
| 无溢出恢复 | API 报 context overflow 直接失败 | opencode / claude-code / gsd-2: 移除错误消息 + 压缩 + 自动重试 1 次 | P1 ✅ |
| Token 估算不准 | chars/4 启发式 | opencode: JSON.stringify 实际序列化计算（准确但有开销）| P3 |

---

### Feature List（按优先级）

#### P1 — 影响稳定性 ✅ 全部完成（2026-05-27）

**1. 工具结果 Head+Tail 截断** ✅

`_TOOL_RESULT_MAX_CHARS = 20_000`，`_default_output_truncator` 改为 head(10K) + tail(10K)，中间插 `[... N 字符已省略 ...]`。
- 文件：`executors/plugins/workspace_tools.py`

**2. 跨 run 历史压缩（Pre-run Compaction）** ✅

每次 `run()` 开始时先执行 Strategy 1 微压缩，再检查 token 量，超过 `_PRE_RUN_TOKEN_THRESHOLD`（20K）则调 `compact.compact_conversation` 压缩。
- 文件：`executors/compact.py`；`executors/plugins/tool_loop_v1.py`

**3. API 溢出恢复（Overflow Recovery）** ✅

新增 `AIContextOverflowError(AIError)` 子类（`ai_client.py`），在三个捕获点（工具循环、`_stream_final`、`_finalize_reply`）均改用 `compact.compact_conversation` 压缩后重试。
- 文件：`ai_client.py`；`executors/plugins/tool_loop_v1.py`

---

#### P2 — 提升质量 ✅ 全部完成（2026-05-27，AutoCompact 重写）

**4. 自适应 Token 阈值（替换固定比例）** ✅

Claude Code 公式：`effectiveWindow = contextWindow - MAX_OUTPUT_TOKENS_FOR_SUMMARY(20K)`；`threshold = effectiveWindow - AUTOCOMPACT_BUFFER_TOKENS(13K)`。Snip 在 70% 窗口时提前触发。
- 文件：`executors/compact.py` `autocompact_threshold()` / `snip_threshold()`

**5. 结构化压缩摘要模板（9 段）** ✅

参考 Claude Code `BASE_COMPACT_PROMPT`，完整 9 段：Primary Request / Key Technical Concepts / Files and Code Sections / Errors and Fixes / Problem Solving / All User Messages / Pending Tasks / Current Work / Optional Next Step。带 `<analysis>` 草稿区（压缩后 strip）和 `<summary>` 正文。
- 文件：`executors/compact.py` `_COMPACT_SYSTEM_PROMPT` / `format_compact_summary()`

**6. DB 历史软删除归档** ✅

`maybe_compact_db_history()`：post-run 后台任务，超 30K tokens 时生成 9 段摘要存入 DB，老消息 `is_deleted=1` 软删除；下次 run 加载时 `_bot_recent()` 过滤 deleted，看到的是摘要 + 最近 10 条消息。
- 文件：`executors/compact.py`；`database.py` `save_compaction_summary()`；`bot_orchestrator.py`

---

#### P3 — 精细化，有余力再做

**6. Token 估算精度提升**

把现有的 `chars/4` 启发式替换为基于实际序列化的估算：

```python
import json

def _estimate_tokens_accurate(messages: list) -> int:
    """Estimate tokens via JSON serialization (opencode approach)."""
    try:
        return len(json.dumps(messages, ensure_ascii=False)) // 4
    except Exception:
        return _estimate_tokens(messages)  # fallback to existing
```

- 参考：opencode `Token.estimate(JSON.stringify(msgs))`
- 好处：序列化后的字符串长度更接近 API 实际计费 token 数，避免压缩阈值误判
- 代价：每次估算需要序列化整个消息数组，有轻微开销；可只在压缩决策时使用精确估算，循环内继续用启发式

**7. 压缩后关键文件重注入**

压缩后从摘要中提取"相关文件"列表，重新读取文件内容注入 context：

```python
POST_COMPACT_MAX_FILES = 5
POST_COMPACT_MAX_TOKENS_PER_FILE = 5_000
POST_COMPACT_FILES_BUDGET = 25_000
```

- 参考：claude-code 压缩后重注入逻辑
- 好处：压缩不会让 bot 忘掉它正在操作的文件内容

**7. 文件操作跨压缩持久跟踪**

在每次压缩时记录本次 run 读写过的文件路径，写入压缩摘要的 XML 标签：

```xml
<read-files>
/path/to/file1.py
</read-files>
<modified-files>
/path/to/file2.py
</modified-files>
```

- 参考：gsd-2 `CompactionDetails { readFiles, modifiedFiles }`，跨压缩累积

---

### 四个框架横向对比

| 维度 | claw-code | opencode | claude-code | gsd-2 |
|---|---|---|---|---|
| **Token 估算** | chars/4 | JSON 序列化（精确） | chars/4 | chars/4 |
| **触发阈值** | 绝对值 10K | window - 13K | window - 13K | window - 16,384 或百分比 |
| **触发时机** | API 前同步 | API 后异步 | API 后异步 | API 后异步 |
| **保留策略** | 固定 4 条消息 | 25% usable（2K~8K tokens）| 2~4 轮 | 固定 20K tokens |
| **工具结果截断** | 160 chars（摘要时）| 头部 2K | 头部 + 图片替换 | **Head+Tail 各 1K** |
| **溢出恢复** | 无 | 有（重试 1 次）| 有（重试 1 次）| 有（重试 1 次）|
| **摘要格式** | 结构化（scope/timeline/文件）| 自由生成 | **模板化（6 个字段）**| 自由生成 + 文件 XML |
| **文件跟踪** | 8 个关键文件 | 无 | 压缩后重注入 5 个 | **跨压缩持久跟踪** |
| **扩展性** | 低 | 高（plugin hook）| 中 | 高（extension hook）|

---

### 实现顺序建议

```
P1（稳定性）✅ 全部完成:
  1. Head+Tail 截断     ✅ workspace_tools.py _default_output_truncator，20K chars head+tail（各 10K）
  2. 溢出恢复           ✅ ai_client.py AIContextOverflowError + compact.compact_conversation 三点覆盖
  3. 跨 run 压缩检查    ✅ pre-run Strategy 1 + compact_conversation，20K token 阈值

P2（质量）✅ 全部完成（AutoCompact 重写）:
  4. 自适应 Token 阈值  ✅ compact.autocompact_threshold()，Claude Code 公式
  5. 结构化摘要模板     ✅ compact._COMPACT_SYSTEM_PROMPT，9 段 + <analysis> 草稿区
  6. DB 历史归档        ✅ compact.maybe_compact_db_history()，软删除 + save_compaction_summary

AutoCompact 5 策略（参考 Claude Code，2026-05-27）✅:
  Strategy 1  计数式微压缩      ✅ compact.apply_tool_result_microcompact()，保留最近 5 个工具结果
  Strategy 2  Snip              ✅ compact.snip_if_needed()，70% 窗口阈值，保留最近 4 对对话
  Strategy 3  Session Memory    ✅ compact._try_session_memory_compact()，增量摘要复用已有摘要
  Strategy 4  AI 全量摘要       ✅ compact._ai_compact()，9 段结构化 + format_compact_summary()
  Strategy 5  Cached Microcompact ✅ ai_client._once_claude() context_management + beta header，仅 Claude provider

P3（精细化）:
  6. Token 估算精度提升   ✅ compact.estimate_tokens() 已用 json.dumps 序列化长度 // 4（opencode 方式）
  7. 文件操作跨压缩跟踪   ✅ tool_loop_v1 _file_tracker dict + compact.build_file_tracker_xml()，
                             afterToolCall 钩子记录 read/write 路径，压缩时写入 XML 重注入（gsd-2 方式）
  8. 压缩后文件重注入（从摘要提取）✅ compact.build_file_contents_for_reinject()：从 _file_tracker 读取
                                     modified 优先、read 次之，最多 5 文件 / 25K 预算；相对路径解析到
                                     bot_ws(bot_id)；_build_reinject() 组合 context_text + ft_xml + file_contents
```

---

---

## 待实现 Feature 清单（按影响面 × 实现成本排序）

> 影响面：⬆ 高 / ➡ 中 / ⬇ 低　实现成本：S 小 / M 中 / L 大 / XL 极大

| 优先 | 功能 | 影响面 | 成本 | 状态 | 说明 |
|------|------|--------|------|------|------|
| 1 | 图片理解 | ⬆ | S | ✅ | `file_url`/`file_type` 经 WebSocket payload → `dispatch_bots` → `ExecutionContext`；`build_image_content` 按 provider 构造多模态块；`_to_claude_messages` 自动转 Claude source.url 格式 |
| 2 | 定时任务（cron / heartbeat） | ⬆ | M | ✅ | 独立 `scheduler/` 插件；APScheduler AsyncIOScheduler；`runner.py` 单点耦合；6 REST 端点；`migration_003` cron_jobs 表；删除模块零残留 |
| 3 | 压缩后文件重注入（从摘要提取） | ➡ | S | ✅ | `compact.build_file_contents_for_reinject()`：modified 优先，最多 5 文件 / 25K 预算，相对路径解析到 `bot_ws(bot_id)`；`_build_reinject()` 组合三段内容 |
| 4 | 工具并发执行 | ➡ | M | ✅ | `ToolDef.concurrency_safe` 标记；只读工具 `asyncio.gather()` 并行；写入工具串行 |
| 5 | Hook 条件过滤 | ➡ | M | ✅ | `_HookEntry(fn, condition)` 存储条件；`_condition_matches()` 用 fnmatch glob 匹配工具名+任意参数值；`add_before/after_hook(condition=)` 新增关键字参数；向后兼容（condition=None = 始终运行） |
| 6 | 用户 Abort | ➡ | M | ✅ | WS `abort` 消息 → `bg.abort_group(group_id)` 取消该群整组任务（dispatch + runner 派生的工作流推进单元，见 DFT-027；旧 `_running_tasks` 只存最初 dispatch 且漏工作流游离任务）；`tool_loop_v1` 捕获 `CancelledError` 广播 `stream_aborted` 后 re-raise；`run_shell` 捕获后 `proc.kill()` 再 re-raise |
| 7 | react_v1 插件 | ⬆ | L | ✅ | Thought → Action → Observation 循环；自由文本解析；重复 Action 保护；react_* WS 事件 |
| 8 | 权限系统：基础规则模型 | ➡ | M | ✅ | `permissions/models.py`：Rule / Ruleset / _PendingRequest；独立包隔离 |
| 9 | 权限系统：决策 Pipeline | ➡ | M | ✅ | `permissions/engine.py`：bypass→deny→allow→dontAsk→子Agent拒绝→ask挂起 |
| 10 | 权限系统：规则持久化 + 前端 UI | ➡ | M | ✅ | SQLite always / 内存 once；`PermissionRequestModal.jsx` 弹窗；`permissions/routes.py` CRUD API |
| 11 | 权限系统：全局权限模式 | ➡ | S | ✅ | Bot 配置页三档切换（default/bypass/dontAsk）；写入 executor_config.permission_mode |
| 12 | 权限系统：Subagent 权限继承 | ➡ | M | ✅ | spawn_agent 透传父 ruleset（含 bypass 模式）；spawn_depth>0 时 ask→deny |
| 13 | 后台 spawn_agent | ➡ | L | ✅ | `spawn_agent(background=True)` 立即返回 task_id；`_run_bg_agent` 用父 broadcaster 并发执行；完成后 `parent_steer.put(result)` 注回；`_bg_tasks` 字典跟踪清理；`execution_ctx["steer_channel"]` 传递父队列引用 |
| 14 | once / asyncRewake Hook | ➡ | M | ✅ | `_HookEntry.once=True` 触发后自动摘除；`add_before/after_hook(once=True)`；asyncRewake：after-hook 向 `context["rewake_queue"]` put 消息，tool_loop_v1 每轮 drain 并以 `[系统唤醒]` 注入对话，广播 `rewake_injected` WS 事件 |
| 15 | 子 Agent 任务恢复 | ➡ | L | ⬜ | `spawn_agent` 传 `session_id` 可复用已有子 Agent context，续传而不重建；需 session store + ExecutionContext 序列化 |
| 16 | 项目知识库集成 | ⬆ | L | ⬜ | Bot 创建时绑定项目知识来源；对话时双轨检索（项目 KB + 个人记忆）；需向量库 + 检索管道 |
| 17 | 代码执行沙箱（容器隔离） | ➡ | L | ⬜ | Docker 容器执行，每次起/销毁；挂载 `bot_{id}/workspace/`；替换现有 run_shell 方案 |
| 18 | 可视化工作流编排（n8n 风格） | ➡ | XL | ⬜ | 拖拽配置多 Bot 协作流程；纯 UX 增强，功能层已支持 |
| 19 | Azure OpenAI 企业认证 | ⬇ | M | ⬜ | Device Code Flow + token refresh；企业内网场景专用；改动 `ai_client.py` / `main.py` / 前端 |

---

## 设计原则

- **Bot = 人**：每个 Bot 有独立身份、性格、能力曲线，不是可互换的角色实例
- **执行引擎可插拔**：推理循环 / 工具集 / 记忆策略全在插件里，热加载，随时替换
- **插件自包含**：每个插件自带工具定义与能力清单（manifest），不依赖全局注册表
- **文件即身份**：工作区文件（IDENTITY / SOUL / MEMORY）是 Bot 的 source of truth，数据库只存索引
- **知识双轨**：项目知识库（外部参考）+ 个人记忆（经验积累），二者独立演进
- **工具调用是基础设施**：工作区、Skill、沙箱、子 Agent 均依赖 Function Calling，M1 优先建设
