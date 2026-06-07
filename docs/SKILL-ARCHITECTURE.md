# Skill 系统架构 · 全景设计

> 最后更新：2026-06-07
> 状态：已落地实现

---

## 一、四层目录结构

```
workspaces/
│
├── system/skills/                     ◄── L1 GENERAL（平台内置）
│   ├── read-file.md                       所有 Bot 只读，随代码发布
│   ├── write-file.md                      创建方：开发者
│   ├── search-code.md                     激活：自动，always 或懒加载
│   ├── create-skill.md
│   └── run-tests.md
│
├── roles/                             ◄── L3 ROLE（角色专属）
│   ├── developer/skills/                  跟角色走，不跟 Bot 走
│   │   ├── code-review.md                 Bot 创建时按 role 自动复制
│   │   ├── write-unit-test.md             创建方：平台预置 + 用户扩展
│   │   ├── commit-and-pr.md
│   │   └── debug-error.md
│   ├── pm/skills/
│   │   ├── write-spec.md
│   │   └── update-board.md
│   └── qa/skills/
│       └── test-plan.md
│
├── bot_{id}/skills/                   ◄── Bot 私有层
│   ├── manual/                        ◄── 用户手写个人技能（直接创建，立即生效）
│   └── learned/                       ◄── L4 LEARNED（自学沉淀）
│       ├── draft/                         Bot 写入区（不自动生效）
│       │   └── project-conventions.md
│       └── active/                        用户审批后移入（才注入）
│           └── team-preferences.md
│
└── group_{id}/shared/skills/          ◄── L2 GROUP（群组领域）
    ├── dev-setup.md                       群组内所有 Bot 可读
    ├── run-tests.md                       创建方：用户 / 群组内 Bot
    ├── deploy.md
    └── api-conventions.md
```

---

## 二、层级定义

| 层级 | 路径 | 归属 | 内容定位 | 写入方 |
|---|---|---|---|---|
| **L1 General** | `workspaces/system/skills/` | 系统级，全局只读 | 通用能力：文件读写、创建 skill、搜索代码 | 平台内置 |
| **L2 Group** | `group_{id}/shared/skills/` | 群组领域，群组内共享 | 领域通用流程：环境搭建、测试命令、发布规范 | 用户 / Bot |
| **L3 Role** | `workspaces/roles/{role}/skills/` | 角色专属，跟角色走不跟 Bot 走 | 角色能力：Developer → code-review；PM → write-spec | Bot 创建时按 role 自动生成 |
| **Personal** | `bot_{id}/skills/manual/` | Bot 个人手写，私有生效 | 用户手动为该 Bot 创建的私有技能，立即生效 | 用户 |
| **L4 Learned** | `bot_{id}/skills/learned/active/` | Bot 个人沉淀，可控扩展 | Bot 自学的项目规律、团队偏好 | Bot 写 draft/，用户审批后移入 active/ |

---

## 三、Skill 文件结构

### 平铺文件（简单技能）

```
skills/
└── code-review.md
```

```markdown
---
name: code-review
description: 对代码进行系统性 Review，输出问题清单和改进建议
layer: role
role: developer
always: false
when_to_use: 当用户提交代码要求 review 时
status: active
learns: false
max_iterations: 5
---

## Code Review 流程

1. 读取目标文件
2. 检查代码规范、逻辑错误、安全隐患
3. 输出结构化问题清单
```

### 目录结构（复杂技能）

```
skills/
└── commit-and-pr/
    ├── SKILL.md        ← 主入口文件
    ├── steps.md        ← 子步骤文档
    └── templates/
        └── pr-body.md  ← PR 模板
```

### Frontmatter 字段全览

| 字段 | 类型 | 说明 | 里程碑 |
|---|---|---|---|
| `name` | string | Skill 显示名 | M1 ✅ |
| `description` | string | 注入 skill 列表时使用 | M1 ✅ |
| `always` | bool | `true` 则全文常驻 system prompt | M1 ✅ |
| `when_to_use` | string | 帮助模型判断调用时机 | M2 ✅ |
| `max_iterations` | int | 执行时动态扩展循环上限 | M1 ✅ |
| `status` | enum | `active` / `disabled` / `deprecated` | M2 |
| `layer` | enum | `system` / `group` / `role` / `personal` / `learned` | M2 |
| `role` | string | L3 技能归属的角色 | M2 |
| `learns` | bool | 执行后允许 Bot 写回总结到 draft/ | M3 |
| `user-invocable` | bool | `false` 则不出现在 skill 列表，只能 always 激活 | M3 |
| `paths` | string | gitignore 语法，文件路径匹配时自动激活 | M3 |
| `context` | enum | `inline`（默认）/ `fork`（独立子 Agent 执行） | M3 |
| `allowed-tools` | list | 执行时允许使用的工具白名单 | M3 |
| `model` | string | 执行时覆盖模型选择 | M4 |

---

## 四、生命周期状态机

### L1 / L2 / L3 / 用户手写技能

```
创建
 │
 ▼
active ◄──────── re-enable
 │
 │ disable
 ▼
disabled
 │
 │ archive
 ▼
deprecated
```

### L4 Bot 自学技能（两段式审批）

```
Bot 发现可复用规律
 │
 │ write_file → learned/draft/（不注入 prompt）
 ▼
draft
 ├── 用户拒绝 ──► 删除
 │
 │ 用户审批
 ▼
active/（开始注入 prompt）
 │
 │ 用户禁用
 ▼
disabled
```

**触发条件（Bot 何时写 draft）：**
- 用户显式说「记住这个做法」/「把这个加入你的技能」
- Skill frontmatter 声明 `learns: true`，执行后允许写回总结

---

## 五、运行时加载顺序

`list_skills_all()` 按以下顺序扫描并进行合并，后层同名 skill 覆盖前层：

```
L1  system/skills/
     ↓ 合并
L2  group_{id}/shared/skills/
     ↓ 合并
L3  roles/{bot.role}/skills/
     ↓ 合并
L4  bot_{id}/skills/learned/active/
     ↓ 合并
    bot_{id}/skills/manual/（用户手写个人技能，非 learned）
     ↓
     ┌──────────────────────────────────────┐
     │         最终 skill 列表               │
     │                                      │
     │  always: true  → 全文注入 system      │
     │  always: false → XML 元数据懒加载     │
     │  status: disabled → 跳过             │
     └──────────────────────────────────────┘
```

> [!NOTE]
> **编排器 Stage 角色映射机制**
> 在编排流执行时，编排器通过 `current_stage_role` 获取当前 Stage 归属的角色系列（Role Family，如 `dev`, `qa`, `ba` 等），并与 Skill Frontmatter 中的 `stages` 过滤器列表比对。只有与当前 Stage 匹配的角色技能才会被加载注入，以实现不同阶段的技能隔离。

---

## 六、前端管理入口

```
Skill 库面板
├── System（L1）      只读，平台内置，可查看不可编辑
├── Group（L2）       可新增、编辑、删除；按群组隔离
├── Role（L3）        可新增、编辑；按角色分组展示
├── Personal          用户手写私有技能，立即生效
└── Learned（L4）     ⚠️ 待审批队列
                          ├── 确认 → 移入 active/
                          └── 拒绝 → 删除 draft
```

**每个 Skill 的操作：**

| 操作 | L1 | L2 | L3 | L4 |
|---|---|---|---|---|
| 查看 | ✅ | ✅ | ✅ | ✅ |
| 创建 | ❌ | ✅ | ✅ | Bot 自动（draft） |
| 编辑 | ❌ | ✅ | ✅ | ✅（active 后） |
| 启用/禁用 | ✅ | ✅ | ✅ | ✅ |
| 测试运行 | ✅ | ✅ | ✅ | ✅ |
| 审批 | — | — | — | ✅（必须） |
| 删除 | ❌ | ✅ | ✅ | ✅ |

---

## 七、实现路线图

| 功能 | 里程碑 | 说明 |
|---|---|---|
| L1 系统技能目录 + 内置示例 | M2 | `system/skills/` + 首批通用技能文件 |
| L2 群组技能目录 | M2 | `group_{id}/shared/skills/` 初始化，load_context_files 扩展 |
| L3 角色技能模板库 + 自动生成 | M2 | `roles/` 目录 + Bot 创建时按 role 写入 |
| `status` / `layer` frontmatter 字段 | M2 | list_skills() 解析并过滤 disabled |
| list_skills() 四层合并逻辑 | M2 | 按 L1→L2→L3→L4 扫描，同名后覆前，每条带 layer/status/injected |
| Session 注入事件广播 | M2 | tool_loop_v1 执行时广播 skill_loaded 事件（全文/元数据/未注入） |
| Skill 状态面板 UI | M2 | 展示层级、status、本次注入状态；enable/disable 切换；L4 审批队列 |
| L4 draft/active 两段式审批 | M3 | write_file 限制写入 draft/；前端审批队列 |
| `learns: true` frontmatter | M3 | 执行后触发写回 draft/ |
| Skill 管理 UI（浏览 + 编辑 + 审批） | M3 | 前端 Skill 库完整面板 |
| Skill 测试运行 | M3 | 单独执行一次 skill 看输出 |

---

## 八、参考实现

| 框架 | 参考点 | 本地路径 |
|---|---|---|
| claude-code-haha | `user/project/managed` 三级 scope；`paths:` 条件激活；`clearSkillCaches()` 热更新 | `/Users/Nuke/claude-code-haha-main/src/skills/` |
| opencode | 外部 URL skill 源；config-driven 路径扩展 | `/Users/Nuke/opencode/packages/opencode/src/skill/` |
| openclaw | `preemptive-compaction`；skill-per-agent-type 过滤 | `/Users/Nuke/openclaw-main/src/agents/` |
| gsd-2 | `skill-catalog` 安装机制；`skill-manifest` 按工作流类型过滤 | `/Users/Nuke/gsd-2/src/resources/extensions/gsd/` |

---

## 九、设计借鉴点（来自四框架生命周期分析）

| 借鉴点 | 来源 | 应用到我们的设计 |
|---|---|---|
| settings-first + 异步物化 | claude-code-haha | `status` 字段存 frontmatter，加载时读取；状态变更写文件，不依赖内存 |
| policy gates（allow / deny / global） | openclaw | L4 审批机制的 draft / active 两态；system 层只读不可禁用 |
| 查询时过滤而非加载时过滤 | opencode | `list_skills_all()` 返回全集含 disabled，调用方（tool_loop / UI）按需过滤 |
| 9 步卸载状态机 | openclaw | L4 draft → reject 流程参考；未来 skill 删除需清理 frontmatter + 文件 + 缓存 |
| 版本目录 + orphan 7 天清理 | claude-code-haha | skill 编辑历史归档设计参考（M3）；draft 被拒绝后可软删除而非立即清除 |
| Catalog 按技术栈匹配 | gsd-2 | L3 Role Skill 初始化时按 bot.role 自动匹配预置包（developer / pm / qa 等） |

---

## 十、 智能体框架 Skill 系统横向对比

以下是业界四大主流框架（**Claude Code / Claude-haha**、**opencode**、**gsd-2**、**openclaw**）与我们当前项目（**nuke-ai-collaborator**）的 Skill 系统横向对比：

| 维度 / 机制 | Claude Code (TypeScript) | opencode (TypeScript) | gsd-2 (TypeScript/Rust) | openclaw (TypeScript) | 我们的项目 (Python/SQLite) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **1. 目录拓扑与扫描范围** | **三级 Scope 扫描**：<br>1. Managed (.claude/skills)<br>2. User (~/.claude/skills)<br>3. Project (.claude/skills，向根目录追溯)<br>支持 `--add-dir` 手动扩展。 | **配置文件与 URL 双轨制**：<br>1. 全局与项目级目录扫描<br>2. `cfg.skills.paths` 配置文件配置<br>3. `cfg.skills.urls` 远程 URL 动态拉取并缓存在本地。 | **行业标准目录**：<br>1. 全局统一使用 `~/.agents/skills/` (Ecosystem)<br>2. 项目级 `.agents/skills/`<br>3. 兼容旧版 `~/.gsd/agent/skills/` 迁移。 | **两级嵌套目录**：<br>1. 扫描直属 `.md` 技能<br>2. 支持一级子文件夹分组（如 `skills/coze/koze-retrieval/SKILL.md`）<br>3. 支持 Symlink 符号链接加载。 | **4 层覆盖架构 (L1 $\rightarrow$ L4)**：<br>1. L1 General（内置通用）<br>2. L2 Group（群组共享）<br>3. L3 Role（角色专属）<br>4. L4 Learned（自学沉淀）与个人手写。 |
| **2. 物理目录结构** | **技能包文件夹制**：<br>仅支持 `[skill-name]/SKILL.md` 目录格式，不扫描根部平铺的单体 `.md` 文件（legacy commands 除外）。 | **SKILL.md 规范**：<br>外部/远程包必须包含 `SKILL.md` 入口文件，且不支持单体 `.md` 平铺加载。 | **混合扫描**：<br>1. 根目录下支持平铺的单体 `.md` 文件<br>2. 子目录下必须使用 `SKILL.md` 格式。 | **SKILL.md 规范**：<br>子目录中必须有且仅有一个 `SKILL.md` 入口文件。 | **混合扫描**：<br>1. 支持直接平铺的单体 `.md` 文件（如 L1-L3 基础技能）<br>2. 复杂技能支持以目录为包进行入口加载。 |
| **3. 重名去重与冲突解决** | **先入为主 (First-Wins)**：<br>通过 `realpath` 解析 canonical 真实物理路径，排队去重，忽略后续同名/同物理文件的加载并记录 Log。 | **本地覆写 (Local Overrides)**：<br>内置技能（如 customize-opencode）最先注册，随后扫描的本地磁盘技能若重名直接**覆盖**内置技能。 | **冲突诊断警告 (Collision Warning)**：<br>不允许重名。若发生重名，系统生成 `collision` 诊断报告并发出警告，指定 `winnerPath` 与 `loserPath`。 | **物理路径去重**：<br>基于 `realpathSync` 物理路径过滤 duplicate，若存在重名且有冲突直接警告并跳过。 | **层级覆盖 (Layer-Override)**：<br>按 L1 $\rightarrow$ L2 $\rightarrow$ L3 $\rightarrow$ L4 扫描合并，同名技能**后层直接覆写前层**（例如 L4 Learned 会覆盖 L1 System）。 |
| **4. 伴随条件激活与懒加载** | **路径匹配激活 (`paths`)**：<br> frontmatter 中配置 `paths` 过滤规则（gitignore 语法），仅在触碰/修改对应特征文件时才激活注入。 | **按需加载 (Get-on-Demand)**：<br>只在模型请求或 UI 渲染时通过 `get()` 和 `available()` 动态获取内容。 | **触发控制 (`disable-model-invocation`)**：<br>若配置为 `true`，模型不能自动感知识别（不进 XML），仅能通过用户 slash 命令手动触发。 | **模型过滤与容量预算**：<br>在 Prompt 中通过 `<available_skills>` 渲染列表。可按 `agentId` 进行条件过滤和可见性控制。 | **双态注入 Base**：<br>`always: true` 的技能全文常驻 system prompt；`always: false` 的技能仅以 XML/JSON 元数据声明，供 LLM 懒加载。 |
| **5. Prompt 容量控制 (Budget)** | **轻量前置预估**：<br>未激活时仅将 `name`、`description` 等 frontmatter 组成短句参与 Token 估算，不加载 Markdown 实体。 | **元数据渲染**：<br>在系统提示词中仅以 `- name: desc` 简短形式渲染，执行时才加载具体 Body。 | **XML 标准格式**：<br>将可见技能转换为 `<skill><name>...</name><location>...</location></skill>` 注入 Prompt 中。 | **Home 目录压缩 (`~/`) & 熔断**：<br>1. 将技能绝对路径中的 homedir 缩短为 `~/`（单个可节省约 5 字符，防 Token 泄漏与膨胀）<br>2. 限制单文件大小（< 256KB）和总 Prompt 长度（默认 18K 字符）。 | **冷热分流机制**：<br>将元数据 XML 平铺进 System Prompt，减少常驻 Prompt 预算，大模型有需时按名索取。 |
| **6. 安全沙箱与 HIL 防线** | **Shell 指令阻断**：<br>本地技能允许 `!{bash}` 评估；但**绝对禁止** remote MCP 技能评估任何 shell 指令，防 RCE 溢出。 | **角色权限网关 (Permission Gate)**：<br>对 Skill 进行 Permission 安全组划归，评估 Agent 角色，`deny` 用户可阻断特定技能的拉取。 | **前置合规检验**：<br>严格验证 `name === parentDirName`，限制 lowercase-hyphen-only 命名规范，避免任意字符转义漏洞。 | **Symlink 越界阻断 (Escape Guard)**：<br>严格检验 realpath，禁止通过 symlink 逃逸出工作区或允许的安全目录（`allowSymlinkTargets`）。 | **两段式审批防线 (HITL Gate)**：<br>Bot 产生的自学技能（Learned）限制写入 `draft/`（不可注入），需人类在 Web UI 审批后移至 `active/` 生效。 |

---

## 十一、 我们的项目核心代码实现分析

我们项目的 Skill 加载、生命周期和转换管线完全用 Python 在 `backend/skills/` 中实现。以下是各模块的具体代码逻辑与技术特色：

### 1. 多层级扫描与合并优先权 ([discovery.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/skills/discovery.py))
* **层级扫描器 (`_scan_dir_sync`)**：支持两种 Skill 组织形态。既支持子文件夹打包式的技能包目录（如扫描 `name/SKILL.md`，优先提取），也支持根目录下平铺的单体 `.md` 文件和 `.py` 代码技能。
* **多层级与个人 (Manual) 技能加载**：
  - 加载时，个人手写技能主要放置在 `bot_{id}/skills/manual/` 目录下。为了向下兼容，加载器在 `manual/` 子目录不存在或扫描完成后，也会额外扫描 `skills/` 的根目录，但会过滤并忽略 `manual` 和 `learned` 子文件夹。
* **后层覆写与系统保护 (`_list_skills_all_sync`)**：
  - 合并过程中，执行顺序覆盖规则：L1 System $\rightarrow$ L2 Group $\rightarrow$ L3 Role $\rightarrow$ L4 Learned/active $\rightarrow$ Personal。
  - **A1 系统层保护防线**：如果在合并过程中发生低层技能试图重名覆盖系统技能的情况，合并器通过 `Path.is_relative_to(SYSTEM_SKILLS_ROOT.resolve())` 判定物理路径是否在系统目录内。如果是，则**禁止覆写**并产生警告日志，实现硬性的 First-Wins。
  - **A3 局部合并回退 (Stub Fallback)**：支持通过 `is_stub: true` 的元数据来进行局部继承更新，其余重写会替换 content 物理路径与类型。
* **两阶段审批与敏感权限静态审计 (C1 / C2 / C3)**：
  - 扫描 `learned/draft/` 目录时，其状态始终设为 `"draft"`（不注入）。
  - **C1 冲突检测**：若 Draft 名称与已激活的技能同名，则触发 collision warning，标记无法直接生效。
  - **C2/C3 敏感权限审计**：静态扫描 Draft 技能中是否提及高权敏感工具（如 `run_shell` 或 `write_file`），包括 YAML `allowed_tools` 白名单以及 Markdown 正文纯文本提及。若检出，会自动附带 critical 安全诊断警告，并在前端 UI 强制提示用户注意。

### 2. 沙箱边界与目录逃逸防御 ([metadata.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/skills/metadata.py) / [lifecycle.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/skills/lifecycle.py))
为了防止恶意的智能体利用包含路径分隔符的技能名称读取系统敏感文件，我们构建了防御体系：
* **命名安全阀 (`_is_safe_name`)**：
  - 采用正则表达式白名单模式：`^[a-z0-9_-]+$`。
  - 严禁包含大写字母、斜杠、反斜杠、双点 `..` 或任何零字节字符。
  - 此过滤规则作为强验证前置，应用在所有写操作与删除操作的 API 入口处。对于非法技能名，统一返回 `"[非法技能名]"` 错误说明。
* **绝对包含性校验 (`_contained`)**：
  - 对目标路径进行 `.resolve()` 展开以处理任何软链接，强制校验其是否严格保留在 `base` 目录中，从而完全阻断了任何**符号链接越界逃逸（Symlink Escape）**漏洞。

### 3. 技能处理管道与 shell 注入阻断 ([processor.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/skills/processor.py))
* **参数替换机制 (`substitute_arguments`)**：全面兼容 Claude 规范，支持对技能内容中的 `$ARGUMENTS`, `$ARGUMENTS[N]`, `$N` 占位符进行入参替换。如果无占位符但传参非空，会在尾部追加 `ARGUMENTS: {args}`。
* **Shell 动态指令阻断 (安全机制 DFT-022)**：
  > [!IMPORTANT]
  > 我们的框架中**有意不支持执行嵌入式 shell**（与 Claude Code 等框架在加载 Prompt 时允许执行 `!{command}` 进行命令行预替换不同）。
  > 因为如果 Agent 具有 `write_file` 和 `run_skill` 权限，恶意的智能体完全可以自我编写一个带 `!` 执行块的临时技能然后调用它，从而绕过系统的 sandbox 审计阻断。
  > 因此我们决定，**任何 shell 执行必须走标准的 run_shell 权限网关**，技能内容中的 `!` 标记一律被视作静态字符串直通。

### 4. 技能动态运行与元数据注入 ([loader.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/skills/loader.py))
* **技能解析运行 (`run_skill`)**：
  在执行技能时，动态调用 `process_skill_content` 替换参数及 `${SKILL_DIR}` 变量。如果是包目录形式（`SKILL.md`），会自动归纳其同级子文件列表并以 `<skill_files>` 标签附加在 Prompt 后面。
* **执行器副作用注入 (Executor Side-Effects)**：
  执行时，元数据（YAML frontmatter）里包含的控制变量（例如 `max_iterations`, `learns`, `allowed_tools`, `model`, `context: "fork"` 等）会直接作为 side-effects 写入当前的执行上下文 `ctx` 中，用来动态调整大模型执行本次技能时的循环次数上限、大模型选择或白名单工具范围。

### 5. 并发文件锁与自毁竞态消除 ([lifecycle.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/skills/lifecycle.py))
* **多进程/线程互斥锁 (`file_lock`)**：
  - 通过 `file_lock` 上下文管理器，对所有技能的写/删/审批等操作实施跨平台的文件锁定（Unix 使用 `fcntl`，Windows 使用 `msvcrt`）。
  - **防止自毁竞态（Anti-Inode Unlink Race）**：传统 flock 模式下在 `finally` 块中执行 `unlink` 锁文件的做法，容易引发 inode 被删除导致后续进程获取不同 inode 互斥锁的竞态。
  - **解决方案**：文件锁统一在系统的 `/tmp/nuke_skill_locks/` 目录下管理，通过对目标技能文件绝对路径计算 SHA-256 得到固定的锁文件名（如 `[sha256].lock`）。锁定生命周期结束时**绝不执行 unlink**，从而彻底避免了 Inode 重用及自毁竞态问题。
