# 架构设计文档

> 最后更新：2026-05-24
> 项目：nuke-ai-collaborator

---

## 一、消息触发链

```
用户发消息
  → main.py WebSocket 收到
  → 取 group_info（名称、公告）
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

## 九、 基础设施与状态管理 (Infrastructure & State Management)

项目致力于提供“零依赖、开箱即用”的单机协同体验，在架构设计上做出了以下核心决策：

### 1. 架构定位：极致单体 (Resilient Monolith)
*   **永久单机方案**：放弃水平扩展（Horizontal Scaling）需求，转向追求单机的**低延迟**、**高吞吐**和**高可靠性**。
*   **No-Redis 决策**：为了降低部署门槛，不引入 Redis 或 NATS 等外部中间件。所有状态管理由 **Python 内存 + SQLite** 共同承担。

### 2. 状态存储模型
*   **内存态 (Transient State)**：活跃 Bot 锁 (`active_bot`)、Steer 消息队列 (`_steer_queues`)、WebSocket 连接池。
    *   *优势*：极速响应，无序列化开销。
*   **持久态 (Persistent State)**：消息记录、Token 统计、会话 Checkpoint、定时任务配置。
    *   *优势*：崩溃可恢复，数据永久化。

### 3. 并发控制与性能优化
*   **SQLite WAL 模式**：通过 `PRAGMA journal_mode=WAL;` 开启预写日志，实现读写并发，解决高频聊天时的锁竞争。
*   **异步写入队列**：高频/非关键写入操作（如日志追加、统计更新）通过内存 Queue 缓冲，由单后台任务顺序写入 SQLite，保护数据库不被击穿。

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

