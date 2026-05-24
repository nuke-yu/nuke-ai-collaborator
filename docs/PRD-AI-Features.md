# PRD — AI 功能路线图

> 最后更新：2026-05-24
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
| MEMORY.md 长期记忆 | M2 | ⬜ 未做 | 用户手写、永不覆盖，记录能力图谱和项目经历；文件存于私有工作区，见「工作区层」 |

---

### 六、知识层 Knowledge

| 功能 | 里程碑 | 状态 | 说明 |
|---|---|---|---|
| 项目知识库集成 | M2 | ⬜ 未做 | Bot 创建时绑定项目知识来源，对话时双轨检索（项目 KB + 个人记忆） |

---

### 七、工作区层 Workspace

Bot 的文件系统身份，文件即身份，数据库只存索引。工作区分三层，权限和生命周期各不相同。

**三层结构：**
```
workspaces/
├── bot_{id}/                        # 私有层：Bot 个人，只有自己能读写
│   ├── IDENTITY.md                  # 角色定义，startup 注入 system prompt
│   ├── SOUL.md                      # 价值观 / 行事原则
│   ├── BOOTSTRAP.md                 # 每次会话开始时执行的初始化指令
│   ├── MEMORY.md                    # 长期手写记忆，永不覆盖（M2）
│   ├── skills/
│   │   ├── code_review.md           # .md 技能：作为提示词返回
│   │   └── deploy.py                # .py 技能：代码沙箱执行（M3）
│   └── logs/
│       └── YYYY-MM-DD.md            # 每日执行日志
│
└── group_{id}/                      # 共享层：群组所有成员可读写
    └── shared/
        ├── BOARD.md                 # ⭐ 任务看板（见下方设计）
        ├── SPEC.md                  # PM Bot 写的需求文档
        ├── API_CONTRACT.md          # 架构 Bot 定的接口约定
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
| 日志写回 | M2 | ⬜ 未做 | 每次执行结束写入 `logs/YYYY-MM-DD.md` |
| MEMORY.md 长期记忆 | M2 | ⬜ 未做 | 用户手写、永不覆盖，记录能力图谱和项目经历 |
| 群组共享工作区初始化 | M2 | ✅ 已完成 | 群组创建时自动建 `group_{id}/shared/`，生成 BOARD.md / SPEC.md / deliverables/ |
| BOARD.md 任务看板 | M2 | ✅ 已完成 | 群组创建时自动生成，Bot 通过 write_file 工具自主维护状态 |
| deliverables/ 交付物目录 | M2 | ✅ 已完成 | 群组共享工作区初始化时自动创建 |
| Skill 注入格式升级（XML） | M2 | ✅ 已完成 | `<available_skills>` XML 块注入，支持 `when_to_use` 字段 |
| Skill `when_to_use` 字段 | M2 | ✅ 已完成 | frontmatter 解析 + XML 注入时带入 |
| Skill 热更新 | M3 | ⬜ 未做 | 监听 `skills/` 目录变化，自动失效缓存，免重启（参考 OpenClaw chokidar） |
| Skill token 预算控制 | M3 | ⬜ 未做 | 注入 skill 列表时限制总字符数（参考 Claude Code 1% context window） |
| Skill 路径条件激活（`paths:`） | M3 | ⬜ 未做 | frontmatter 配置 glob 路径，当工作区文件匹配时自动激活该 skill |
| Skill Fork 子 Agent（`context: fork`） | M3 | ⬜ 未做 | skill 在独立 token 预算的子 Agent 中执行，结果回传主流程 |
| 工作流执行日志归档 | M3 | ⬜ 未做 | `group_{id}/runs/` 记录每次工作流执行的上下文和过程，供事后追溯 |

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
| 代码执行沙箱 | M3 | ⬜ 未做 | Bot 在工作区隔离环境执行代码，结果回显到聊天 |

---

### 九、协作层 Collaboration

| 功能 | 里程碑 | 状态 | 说明 |
|---|---|---|---|
| 自定义工作流（关键词驱动角色链） | M0 | ✅ 已完成 | done_keyword 推进，顺序 + 并行 |
| 并行任务池（多开发者抢单） | M0 | ✅ 已完成 | TICKETS 格式，队列认领 |
| @all 顺序协作 | M0 | ✅ 已完成 | 同角色竞速，不同角色顺序执行 |
| Before-finalize 质量钩子 | M3 | ⬜ 未做 | 回复前触发审查 Bot，不满意可打回重做（含 retry budget） |
| 子 Agent 派生 | M3 | ⬜ 未做 | Bot 在回复中主动派生子任务给其他 Bot，支持深度限制 |
| 定时任务（cron / heartbeat） | M3 | ⬜ 未做 | Bot 可配置周期性执行，支持轻量 bootstrap 模式 |

---

### 十、平台层 Platform

| 功能 | 里程碑 | 状态 | 说明 |
|---|---|---|---|
| 可视化工作流编排（n8n 风格） | M4 | ⬜ 未做 | 拖拽配置多 Bot 协作流程 |
| Azure OpenAI 企业认证（Device Code Flow） | M2 | ⬜ 未做 | 见下方说明 |

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
- `backend/ai_client.py` — 新增 `azure` provider，用 Bearer token 调 Azure OpenAI，token 自动刷新
- `backend/main.py` — 新增 `GET /api/auth/azure/start`、`GET /api/auth/azure/status` 端点
- `frontend/ApiKeyManager.jsx` — 新增「Azure 企业登录」入口 + device code 弹窗 + 状态轮询

---

## 里程碑规划

| 里程碑 | 主题 | 核心交付 | 依赖 |
|---|---|---|---|
| **M0** 已交付 | 基础 Bot 能力 | 身份 / 人格 / 智能 / 工作流 / 个人记忆 | — |
| **M1** 当前 | 引擎 + 工具基础设施 | 执行引擎插件框架 / simple_v1 / tool_loop_v1 / Function Calling / 工作区文件系统 / Skill 发现 + always 注入 | M0 |
| **M2** 下一步 | 知识 + 协作工作区 | 项目知识库集成 / MEMORY.md / 日志写回 / 群组共享工作区 / BOARD.md 任务看板 / Skill XML 格式 / Azure 企业认证 | M1（工作区） |
| **M3** 中期 | 质量 + 自主性 | 代码沙箱 / 质量钩子 / 子 Agent / 定时任务 / Skill 热更新 & 路径激活 & Fork | M1（Function Calling） |
| **M4** 长期 | 平台化 | react_v1 插件 / 可视化工作流编排 | M1~M3 |

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

## 设计原则

- **Bot = 人**：每个 Bot 有独立身份、性格、能力曲线，不是可互换的角色实例
- **执行引擎可插拔**：推理循环 / 工具集 / 记忆策略全在插件里，热加载，随时替换
- **插件自包含**：每个插件自带工具定义与能力清单（manifest），不依赖全局注册表
- **文件即身份**：工作区文件（IDENTITY / SOUL / MEMORY）是 Bot 的 source of truth，数据库只存索引
- **知识双轨**：项目知识库（外部参考）+ 个人记忆（经验积累），二者独立演进
- **工具调用是基础设施**：工作区、Skill、沙箱、子 Agent 均依赖 Function Calling，M1 优先建设
