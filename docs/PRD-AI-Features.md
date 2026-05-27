# PRD — AI 功能路线图

> 最后更新：2026-05-27（Context P1 三项全部实现）
> 项目：nuke-ai-collaborator

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
| 图片理解 | M4 | ⬜ 未做 | 上传图片时携带 URL 给视觉模型 |

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
| Context Compaction（上下文自动压缩） | M1 | ✅ 已完成 | 上下文超 60K 字符时自动 AI 摘要压缩，保留末尾 8 条消息，广播 compaction 事件（参考 GSD-2 CompactionOrchestrator） |
| `react_v1` 插件 | M4 | ⬜ 未做 | ReAct 推理循环：Thought → Action → Observation |

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
| L3 Role Skills（角色专属技能） | M2 | ✅ 已完成 | `roles/{role}/skills/` 预置技能：developer(4) / pm(2) / qa(2)；`list_skills_all()` 运行时按 role 读取，跟角色走不跟 Bot 走 |
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
| `user-invocable` | M2 | `false` 则不出现在 skill 列表，只能 `always` 方式激活 |
| `argument-hint` | M3 | 调用参数提示，注入到 skill 列表 |
| `paths` | M3 | gitignore 语法，文件路径匹配时自动激活（参考 Claude Code） |
| `context` | M3 | `inline`（默认）/ `fork`（独立子 Agent 执行） |
| `allowed-tools` | M3 | skill 执行时允许使用的工具白名单 |
| `model` | M4 | skill 执行时覆盖模型选择 |

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
| afterToolCall 结果钩子 | M3 | ✅ 已完成 | `tool_executor.add_after_hook(hook)`，签名 `async (name, arguments, result, context) -> str | None`；链式执行，返回新字符串则替换结果，返回 None 保持不变；内置 `_default_output_truncator`：单条工具结果超 20,000 字符时截断并附注省略字符数，防止 context window 被大输出塞满 |
| 代码执行沙箱（subprocess） | M3 | ✅ 已完成（重新定义） | 对齐 claude-code / opencode 设计，不直接执行 `.py`；改为 skill 目录结构（`SKILL.md` + 伴随脚本），`processor.py` 处理管道支持 `` ```! `` / `` !`cmd` `` 在 prompt 中嵌入 shell 命令并在发给 AI 前执行替换，AI 通过 `run_shell` 执行目录内脚本；flat `.py` stub 提示路径。无需 subprocess 沙箱，天然跨平台 |
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
| 定时任务（cron / heartbeat） | M4 | ⬜ 未做 | Bot 可配置周期性执行，支持轻量 bootstrap 模式 |

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
| 工具结果截断丢尾部 | 超 20K 整体砍头，尾部关键信号（exit code / pass/fail）丢失 | gsd-2: head+tail 各 1K，中间插 `[... N more ...]` | P1 ✅ |
| 跨 run 历史不压缩 | Compaction 只在 tool loop 内触发，两次对话之间历史无限增长 | opencode / gsd-2: post-API 异步检查，每次 run 结束都评估 | P1 ✅ |
| 保留策略粗糙 | 固定保留末尾 6 条消息 | gsd-2: 按 token 预算保留（20K tokens），自动适应消息长短 | P2 |
| 压缩摘要质量低 | 自由生成摘要，无结构约束 | claude-code: 模板化（目标 / 进展 / 关键决策 / 下一步 / 相关文件）| P2 |
| 压缩后不恢复文件 | 压缩后关键文件内容丢失 | claude-code: 压缩后重注入最多 5 个关键文件（25K token 预算）| P3 |
| 无溢出恢复 | API 报 context overflow 直接失败 | opencode / claude-code / gsd-2: 移除错误消息 + 压缩 + 自动重试 1 次 | P1 ✅ |
| Token 估算不准 | chars/4 启发式 | opencode: JSON.stringify 实际序列化计算（准确但有开销）| P3 |

---

### Feature List（按优先级）

#### P1 — 影响稳定性 ✅ 全部完成（2026-05-27）

**1. 工具结果 Head+Tail 截断** ✅

`_TOOL_RESULT_MAX_CHARS = 2_000`，`_default_output_truncator` 改为 head(1K) + tail(1K)，中间插 `[... N 字符已省略 ...]`。
- 文件：`executors/plugins/tool_loop_v1.py` 第 375 行

**2. 跨 run 历史压缩（Pre-run Compaction）** ✅

每次 `run()` 开始时，在 stream_start 后检查 DB 加载的初始 `messages` token 量。超过模型窗口 50% 阈值则调 `_compact_messages` 压缩后再进入工具循环。
- 文件：`executors/plugins/tool_loop_v1.py` 第 593 行

**3. API 溢出恢复（Overflow Recovery）** ✅

新增 `AIContextOverflowError(AIError)` 子类（`ai_client.py`），在 `_once_openai_compat` / `_once_claude` 中检测 400/413 响应体中的 overflow 关键词。工具循环捕获后移除末尾 assistant 消息 → 压缩 → 重试 1 次；二次失败则降级为普通 AIError。
- 文件：`ai_client.py` 第 14 行；`executors/plugins/tool_loop_v1.py` 第 661 行

---

#### P2 — 提升质量，值得做

**4. Token 预算保留策略（替换固定 6 条）**

把 `_COMPACTION_KEEP_RECENT = 6` 改为 token 预算方式：

```python
COMPACTION_KEEP_RECENT_TOKENS = 20_000  # gsd-2 默认值

# 从末尾倒序累加 token，直到预算用完
def _find_keep_from(messages, budget=COMPACTION_KEEP_RECENT_TOKENS):
    total = 0
    for i in range(len(messages) - 1, -1, -1):
        total += _estimate_tokens([messages[i]])
        if total > budget:
            return i + 1
    return 0
```

- 参考：gsd-2 `COMPACTION_KEEP_RECENT_TOKENS = 20_000`
- 好处：消息短时保留更多条，消息长时不保留太多，自动适应

**5. 结构化压缩摘要模板**

给压缩 AI 调用加模板约束，确保摘要可机读、可复用：

```
请将以下对话历史压缩为结构化摘要，使用以下格式：

## 目标
（本次对话用户想达成什么）

## 已完成
（已执行的操作和产出）

## 进行中 / 阻塞
（尚未完成的任务）

## 关键决策
（重要的技术或产品决定）

## 下一步
（明确的后续行动）

## 相关文件
（被读取或修改过的文件路径）
```

- 参考：claude-code `sessionMemoryCompact.ts` 模板
- 好处：摘要结构稳定，下次 run 注入时 AI 能快速定位"上次做到哪了"

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
P1（稳定性）✅ 已完成:
  1. Head+Tail 截断     ✅ tool_loop_v1.py _default_output_truncator，2K chars head+tail
  2. 溢出恢复           ✅ ai_client.py AIContextOverflowError + tool_loop_v1 重试逻辑
  3. 跨 run 压缩检查    ✅ tool_loop_v1 pre-run compaction，窗口 50% 阈值

P2（质量）:
  4. Token 预算保留     ← 替换 _COMPACTION_KEEP_RECENT，1小时
  5. 结构化摘要模板     ← 修改 _compact_messages prompt，1小时

P3（精细化）:
  6. Token 估算精度提升 ← 改用 JSON 序列化，1小时
  7. 压缩后文件重注入   ← 需要文件提取 + workspace 读取
  8. 文件操作跟踪       ← 需要工具调用钩子配合
```

---

## 设计原则

- **Bot = 人**：每个 Bot 有独立身份、性格、能力曲线，不是可互换的角色实例
- **执行引擎可插拔**：推理循环 / 工具集 / 记忆策略全在插件里，热加载，随时替换
- **插件自包含**：每个插件自带工具定义与能力清单（manifest），不依赖全局注册表
- **文件即身份**：工作区文件（IDENTITY / SOUL / MEMORY）是 Bot 的 source of truth，数据库只存索引
- **知识双轨**：项目知识库（外部参考）+ 个人记忆（经验积累），二者独立演进
- **工具调用是基础设施**：工作区、Skill、沙箱、子 Agent 均依赖 Function Calling，M1 优先建设
