# 架构设计文档

> 最后更新：2026-05-24
> 项目：nuke-ai-collaborator

---

## 一、消息触发链（分片宇宙版）

```
用户发消息
  → main.py (Supervisor) WebSocket 终止
  → 路由匹配 (CELL-15) -> 确定 Worker_N
  → select_triggered_bots()   判断哪些 Bot 需要响应（@mention / @all / 工作流）
  → dispatch_bots()            构建 ExecutionContext，传入群组信息
  → registry.get(executor_id).run(ctx)   由 executor_id 决定走哪个插件
```

---

## 二、ExecutionContext — Bot 运行时上下文

每次 Bot 被触发，都会构建一个 `ExecutionContext` 传入执行引擎：

```python
@dataclass
class ExecutionContext:
    bot               # Bot 自身配置（DB 字段：system_prompt / role / model 等）
    group_id          # 群组 ID（对应页面上的群组）
    group_name        # 群组名称
    group_announcement# 群组公告
    all_members       # 群组全体成员（人类 + Bot）
    all_bots          # 群组内所有 Bot
    sender            # 发消息的人
    history           # 最近 8 条消息（OpenAI format）
    workflow_suffix   # 当前工作流阶段指令（未激活时为空）
    broadcaster       # WebSocket 广播器（流式输出用）
```

---

## 三、System Prompt 组装顺序

### 两个插件共同部分

```
1. bot.system_prompt
   角色核心定义（数据库字段）
   + personality_prompt（5 维性格滑块生成的行为指令）

2. memory
   向量记忆检索结果，按当前消息相关性排序
   历史对话中自动积累的经验（Chroma + 摘要）

3. 【群组信息】
   群组：电商项目
   公告：本周冲刺目标：完成支付模块
   人类成员：Nuke
   AI 成员：小明（后端工程师）、小红（测试工程师）

4. workflow_suffix（仅工作流激活时）
   "当前阶段：开发。完成后在最后一行写：开发完毕"
```

### user message 前缀（tool_loop_v1 独有）

工作区内容**不**注入 system prompt，而是作为 user 消息前缀，参考 Claude Code 的设计。

```
5. 【工作区文件】
   按顺序加载，bot 私有文件先出现，group 共享文件追加在后（权重更高）：
     === AGENT.md ===         bot 私有，推理框架与行为边界
     === BOOTSTRAP.md ===     bot 私有，每次启动时执行的指令
     === IDENTITY.md ===      bot 私有，角色定义
     === AGENT.md (群组) ===  group 共享层（如果存在，追加覆盖）

6. 【可用技能】
   skills/ 目录扫描，仅注入元数据（名称 + 摘要）
   全文懒加载：AI 决定调用时才通过 run_skill 工具读取完整内容
     - code_review: Code Review 技能
     - deploy: 部署检查清单

7. 用户原始消息
   "[Nuke]: @小明 帮我看下这个接口"
```

---

## 四、工作区文件体系

### 目录结构

```
workspaces/
├── bot_{id}/                    # Bot 私有层，只有自己能读写
│   ├── IDENTITY.md              # 角色定义，由 system_prompt 生成
│   ├── SOUL.md                  # 价值观与行事原则，由 personality_prompt 生成
│   ├── BOOTSTRAP.md             # 启动脚本，每次上线时执行
│   ├── AGENT.md                 # 推理框架：思考方式、工作原则、行为边界
│   ├── MEMORY.md                # 长期手写记忆，用户维护，永不覆盖（M2）
│   ├── skills/
│   │   ├── code_review/
│   │   │   └── SKILL.md        # 目录结构（优先，参考 OpenCode）
│   │   └── deploy.md           # 平铺文件（向后兼容）
│   └── logs/
│       └── YYYY-MM-DD.md       # 每日执行日志（append_log 写入）
│
└── group_{id}/                  # 群组共享层，所有成员可读写（M2）
    └── shared/
        ├── BOARD.md             # 任务看板：Backlog / 进行中 / 已完成
        ├── SPEC.md              # 需求文档
        ├── API_CONTRACT.md      # 接口约定
        └── deliverables/        # 各 Bot 提交的交付产出
```

### 文件加载时机

| 文件 | 何时加载 | 加载方式 |
|------|---------|---------|
| AGENT.md | 每次 Bot 响应时 | user 消息前缀 |
| BOOTSTRAP.md | 每次 Bot 响应时 | user 消息前缀 |
| IDENTITY.md | 每次 Bot 响应时 | user 消息前缀 |
| SOUL.md | Bot 主动调用 read_file 时 | 工具调用（懒加载）|
| MEMORY.md | 每次 Bot 响应时 | user 消息前缀（startup_files 注入，write_file 写保护）|
| skills/name/SKILL.md | AI 决定调用该技能时 | run_skill 工具（懒加载）|
| logs/YYYY-MM-DD.md | 每次 Bot 响应结束后 | append_log 追加写入 |

### 文件覆盖规则

```
同名文件：bot 私有版本先出现，group 共享版本追加在后
  → AI 上下文中，越靠后的内容权重越高
  → group 版本起"补充 / 覆盖"效果，不完全替换 bot 版本

Skill 文件优先级：
  skills/name/SKILL.md   目录结构（优先，新格式）
  skills/name.md          平铺文件（fallback，向后兼容）
```

---

## 五、执行引擎插件对比

| 能力 | simple_v1 | tool_loop_v1 |
|------|-----------|-------------|
| 工作区文件注入 | ❌ | ✅ user 消息前缀 |
| Skill 发现与调用 | ❌ | ✅ 元数据启动注入，全文懒加载 |
| 群组信息注入 | ✅ system prompt | ✅ system prompt |
| 向量记忆 | ✅ | ✅ |
| 工具调用（Function Calling）| ❌ | ✅ |
| 推理循环（最多 N 轮）| 单次 | ✅ 最多 10 轮 |
| 流式输出 | ✅ | ✅ |

---

## 六、Skill 发现机制（参考 OpenCode）

启动时扫描 `skills/` 目录，只注入元数据：

```
【可用技能】
  - code_review: Code Review 技能
  - deploy: 部署检查清单
使用 run_skill(name="技能名") 调用
```

AI 判断需要某个技能时，通过 `run_skill` 工具触发完整内容加载，
避免把所有技能内容一次性塞入 context window。

---

## 七、群组看板设计（M2）

Bot 使用共享工作区的 `BOARD.md` 作为任务状态的 source of truth：

```markdown
# 工作看板 · 电商项目

## Backlog
| # | 需求 | 优先级 |
|---|------|--------|
| #003 | 权限管理模块 | P1 |

## 进行中
| # | 需求 | 负责人 | 状态 | Todo |
|---|------|--------|------|------|
| #001 | 用户登录 | Dev A | 🔨 开发中 | ☑ schema ☐ JWT ☐ 单测 |

## 已完成
| # | 需求 | 负责人 | 完成时间 | 产出 |
|---|------|--------|---------|------|
| #000 | 数据库初始化 | Dev A | 2026-05-24 | deliverables/schema.sql |
```

多 Bot 协作流程：
```
架构 Bot  → 初始化 BOARD.md，把需求拆成 ticket 写入 Backlog
Dev A/B   → 读 BOARD.md 认领 ticket → 更新状态 → 完成后提交 deliverables/
QA Bot    → 读 BOARD.md 找「已完成」→ 验收 → 更新状态「✅ 验收通过」
```

状态在文件里，不依赖聊天消息传递，Bot 重启不丢失上下文。

---

## 八、设计原则

- **工作区文件注入为 user 消息而非 system prompt** — 参考 Claude Code，对模型更有效，支持动态更新
- **Skill 全文懒加载** — 参考 OpenCode，只在调用时读取，节省 context window
- **群组是 Bot 的环境** — 群组名、公告、成员列表注入 system prompt，Bot 知道自己在哪里、和谁协作
- **文件即状态** — BOARD.md 是任务状态的 source of truth，数据库只存索引
- **层级覆盖** — group 共享文件追加在 bot 私有文件之后，权重更高，实现群组级策略覆盖

---

## 八、 团队协作模式 (Multi-Agent System Workflow)

项目模拟真实的研发团队协作场景，通过群组（Group）将不同角色的 Agent 与人类成员组织在一起：

### 1. 角色链路
- **BA Bot**：分析用户需求，生成架构背景，并创建结构化的任务（Jira Tickets）。
- **架构 Bot**：根据业务背景提供技术选型、数据库设计和分布式方案。
- **开发 Bot (Dev)**：通过领票机制（Claiming）认领任务。支持基于经验值的匹配或随机认领。
- **质量 Bot (QA)**：监听开发提交，自动在本地环境启动验证并反馈测试结果。

### 2. 核心协作逻辑：Event-Driven Task Claiming
不同于简单的对话，Agent 之间通过**内部业务事件**进行深度耦合：
- `TicketCreatedEvent` → 触发 Dev Bot 认领逻辑。
- `CodeCommittedEvent` → 触发 QA Bot 自动测试逻辑。
- `TestPassedEvent` → 触发人类/部署 Bot 介入。

---

## 九、 能力装配系统：Traits vs. Skills (Feature Mounting)

系统采用“静态特征”与“动态技能”分离的双轨制能力装配架构，实现了能力的高复用与低 Token 消耗。

### 1. 核心概念区分
*   **Traits (特征能力/内功)**：
    *   *定义*：被动的、永远生效的行为准则或规范（如“Python 开发规范”、“Jira 协作约定”）。
    *   *机制*：在前端 UI 的“特征”面板勾选。系统在运行时从 `system/traits/*.md` 原子池中提取内容，**静态缝合**到 Bot 的 System Prompt 中。
    *   *优势*：避免重复编写庞大的 Prompt，实现“积木式” Bot 组装。
*   **Skills (业务技能/招式)**：
    *   *定义*：主动的、多步骤的复杂工作流或脚本（如“代码审查”、“执行单元测试”）。
    *   *机制*：通过后端的 `run_skill` 工具**按需懒加载 (Lazy Load)**。系统只向模型提示可用技能的名称，不注入全文，直到被显式调用。
    *   *优势*：极大节省上下文窗口，Bot 可以拥有成百上千的技能而不“失忆”。

### 2. 基于文件树的技能级联解析 (Cascading Directory Resolution)
Skill 不需要手动绑定到 Bot，而是采用**“代码即配置 (Configuration as Code)”**的物理目录覆盖策略。系统按以下优先级叠加载入 `.md` 技能文件：
1.  **Group 级** (`group_{id}/shared/skills/`)：项目专有 SOP，优先级最高。
2.  **Role 级** (`roles/{role_name}/skills/`)：岗位通用的专业能力。
3.  **System 级** (`system/skills/`)：全员通用工具。
4.  **Learned 级** (`bot_{id}/skills/learned/`)：Bot 在对话中自我总结的独家秘籍。

### 3. 热更新与 UI 协同 (Hot Reload via UI)
得益于工作区 (Workspace) 面板的设计，系统的能力赋能是**零停机**的：
用户只需在前端文件树面板找到对应的层级目录（如 `roles/qa/skills/`），新建一个 `.md` 文件并保存。下一秒的对话中，所有 QA 角色的 Bot 将立刻通过 `list_skills_all()` 扫描到并掌握该新技能。

---

## 十、 架构演进路线图 (Phase-based Roadmap)

基于研发团队协作背景，架构优化的优先级分为三个阶段：

### 阶段一：打通信息流（Data Consistency）
- **动态工作区挂载**：解决 `tool_loop` 循环中文件上下文陈旧的问题。确保前序 Bot 写入的需求/代码，后续 Bot 在其推理循环内部能立即感知最新版本。
- **文件变更嗅探**：当 `write_file` 工具被调用时，立即通过消息注入（In-context Injection）更新 Bot 的上下文，防止产生幻觉。

### 阶段二：稳固工作流（Execution Resilience）
- **任务级 Checkpoint**：针对编码、测试等长周期任务，在 SQLite 中记录 `session_checkpoint`。
- **崩溃恢复协议**：服务器重启后，系统能够根据 Checkpoint 指针自动重建 `tool_loop` 状态，恢复挂起的任务，防止“任务僵死”。

### 阶段三：智能化分发（Orchestration Upgrade）
- **事件中心化**：将 `EventBus` 从简单的消息中转升级为“任务编排中心”。
- **领票与竞速逻辑**：完善多 Bot 竞争任务时的加锁与分配算法，支持基于 Bot 技能标签的自动调度。

---

## 十、 基础设施与状态管理 (Infrastructure & State Management)
... (保持原有内容并微调) ...


项目致力于提供“零依赖、开箱即用”的单机协同体验，在架构设计上做出了以下核心决策：

### 1. 架构定位：极致单体 (Resilient Monolith)
*   **永久单机方案**：放弃水平扩展（Horizontal Scaling）需求，转向追求单机的**低延迟**、**高吞吐**和**高可靠性**。
*   **No-Redis 决策**：为了降低部署门槛，不引入 Redis 或 NATS 等外部中间件。所有状态管理由 **Python 内存 + SQLite** 共同承担。

### 2. 状态存储模型与恢复职责 (State Model & Recovery Protocol)

项目中的工作流状态分布在多个层级（内存、SQLite 与本地文件系统），其状态类别、唯一数据源（Source of Truth）以及崩溃恢复协议如下表所示：

| 状态类别 | 唯一 Source of Truth | 崩溃恢复负责方与接续协议 |
| :--- | :--- | :--- |
| **群组与历史消息** | SQLite (`groups`, `members`, `messages` 表) | **DB Layer / db.py**<br>属于底座级持久化状态。服务器重启后由只读/可写连接直接按需读取，无额外的内存常驻缓存。 |
| **工作流编排快照 (Orchestrator Snapshot)** | SQLite `workflow_state` 表中的 JSON Blob | **workflow_store.py**<br>服务器重启时，系统扫描各活跃 Group 的 `workflow_state` 表，读取最新持久化的快照 JSON 并反序列化。 |
| **工作流内存状态 (`orchestrator._state`)** | 从 SQLite 快照 Blob 中重建的反序列化实例 | **core/workflow.py (`resume_workflows`)**<br>系统启动时执行编排状态的自动接续。从 `workflow_state` 表的快照重建编排器内存实体，恢复阶段标记及当前活跃工作单元的触发链。 |
| **工具执行断点 (Tool Checkpoint)** | SQLite `sessions` / `session_events` 表 | **ToolLoopRunner / executors.plugins.tool_loop_v1**<br>保存了 Tool Loop 中每轮执行的 Assistant 响应、工具调用及工具返回结果快照。重启后，`recover_all` 流程匹配对应的 `session_id` 恢复执行历史并接续。 |
| **观点压缩缓存 (Viewpoints Cache)** | 内存字典 `viewpoints_summary` (通过 `orch.get_viewpoints_cache` 获取) | **Compactor / executors.compact (`compress_history`)**<br>作为发言摘要的加速缓存。进程重启后，内存缓存失效；在后续执行中，若历史消息中缺失对应的观点摘要，则由 `compress_history` 触发 Lazy LLM Recap (懒加载重建) 重新填充。 |
| **工作区看板 (`BOARD.md`)** | 本地文件系统 `${workspace}/BOARD.md` | **Orchestrator & Workspace Tools**<br>物理文件即为 Source of Truth。进程重启后物理文件不受影响，各 Bot 通过 `write_file`/`read_file` 直接读取最新的看板物理状态。 |


### 3. 租约转移与生命周期迁移动态保障 (Handoff & Eviction Barrier)
在多 Worker 租约转移（Handoff）或本地生命周期 LRU 驱逐（Eviction）时，存在“旧 Worker 内存态 `orchestrator._state` 已更新（如观点压缩已生成）但尚未因步骤完成而自然落盘”的竞态窗口。
为防止该竞态导致状态丢失或回退，系统实施了以下保障方案：
*   **同步落盘屏障 (Persistence Barrier)**：在 Worker 物理释放租约（`LEASE_RELEASED`）并中止在飞任务（`bg.abort_group`）之前，**必须同步触发一次强制状态序列化与持久化**（调用 `orch.serialize` 强行写入 `workflow_state` 表）。
*   **路由切换与接管**：只有在旧 Worker 的落盘屏障与生命周期清理完全结束后，Supervisor 才更新全局路由缓存并放行新 Worker 的 Hydrate 过程，确保新 Worker 能够 100% 还原最新的编排状态。
*   **在飞任务终止语义**：对于被 eviction 强行中止的在飞执行单元（如 `tool_loop_v1`），恢复时遵从 Chat 交互安全语义——不再自动接续重入，而是由 `sessions.recover_all` 标记为 `failed` 等待用户显式干预。


### 4. 并发控制与性能策略 (Concurrency Strategy)
- **SQLite 优化**：强制开启 WAL 模式 (`PRAGMA journal_mode=WAL;`)，解决顺序执行下的读写冲突。
- **策略性降级 (Strategic Deprioritization)**：
    - **高并发写入队列**：由于研发团队协作的消息频次较低（分钟级而非秒级），暂不实施复杂的后台写入队列，优先保证 WAL 的稳定性。
    - **前端排队 UI**：在团队协作模型中，用户更关注最终产出和 Timeline 历史，而非实时的毫秒级排队状态，因此实时进度条优先级调低。


---

## 十、 单机架构优化建议 (Architecture Recommendations)

针对当前单机架构，提出的核心改进方向：

### 1. 影子持久化 (Shadow Persistence)
*   **目标**：解决进程重启后活跃任务丢失的问题。
*   **方案**：在内存状态更新时，同步（或异步延迟）在 SQLite 中记录当前 Agent 的执行进度（Checkpoint），重启后自动恢复。

### 2. I/O 隔离与响应性
*   **目标**：防止本地工作区（Workspace）大文件读写阻塞 WebSocket 主循环。
*   **方案**：引入 `aiofiles` 或使用 `asyncio.to_thread` 将磁盘密集型操作与网络事件循环隔离。

### 3. 资源生命周期管理
*   **目标**：防止单机长期运行导致的内存泄漏和磁盘膨胀。
*   **方案**：实施基于不活跃时长的会话自动销毁机制，以及定时清理旧日志/临时文件的 Cron Job。

---

## 十一、 架构问题清单 (Architectural Issue List)

| 维度 | 严重程度 | 问题描述 | 建议措施 |
| :--- | :--- | :--- | :--- |
| **并发** | 高 | **SQLite 锁竞争风险**：高并发写入时可能出现 `database is locked`。 | 强制开启 WAL 模式并实施单写者队列。 |
| **性能** | 高 | **阻塞性 I/O**：同步文件操作可能导致 WebSocket 出现毫秒级卡顿。 | 全面异步化磁盘操作。 |
| **可靠性** | 中 | **缺乏任务断点**：Agent 执行复杂 Tool Loop 中途重启无法自动接续。 | 建立会话 Checkpoint 机制。 |
| **安全** | 中 | **敏感信息存储**：API Key 明文存储在本地配置文件中。 | 增加混淆存储或引导用户使用环境变量。 |
| **可观测性**| 低 | **单机状态黑盒**：难以直观监控当前内存任务堆积情况。 | 增加 `/api/system/status` 暴露运行指标。 |

