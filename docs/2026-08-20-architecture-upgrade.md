# 🏛️ Nuke AI Collaborator 架构演进与 DeepSeek Harness 模式吸收报告 (2026-08-20)

> **文档编号**：ADR-2026-08-20-DSH-EVOLUTION  
> **制定日期**：2026-08-20  
> **核心主题**：借鉴 DeepSeek Harness (`dsh`) 工业级架构实践，全面升级 Nuke AI Collaborator 的工具调度、安全门禁、上下文管理与微内核扩展能力。

---

## 📌 1. 背景与技术动机

在深度调研与阅读 DeepSeek 官方开源的下一代智能体运行时 **DeepSeek Harness (`dsh`)** 源码后，我们发现其在**单智能体的高效调度、代码沙箱执行、时空可组合插件微内核、以及严密的工程防破坏设计**上达到了极高的工业级水准。

本报告旨在结合 **Nuke AI Collaborator** 的「组织级常驻 AI 团队协同操作系统」定位，系统性梳理两者的核心差异，并确立在保留 Nuke 现有的 **多进程拓扑、A-MEM 认知记忆复利与 Group 物理隔离** 优势的前提下，如何精准吸收 `dsh` 的顶尖设计模式。

---

## ⚔️ 2. 系统定位与核心能力对比

```
【Nuke AI Collaborator 的定位】            【DeepSeek Harness 的定位】
  🏢 “一个具有严密制度的软件工程团队”         ⚙️ “一把极致精密的手术刀与执行容器”

   PM ➔ BA ➔ Dev ➔ QA 协同流水线                Cordis 微内核插件树
   Git Worktree 独立工单隔离                     Code Mode 批量代码极速调度
   A-MEM 认知记忆复利沉淀                        Read-Before-Mutate 防盲改门禁
   跨进程 SQLite 物理分群隔离                    Event-Sourced 确定性事件流回放
   PR Gate 人工审查准入                          Headless CLI 嵌入 CI/CD
```

### 核心能力对比矩阵

| 能力维度 | 🏢 Nuke AI Collaborator (`nuke`) | ⚙️ DeepSeek Harness (`dsh`) | 吸收与融合决策 |
|:---|:---|:---|:---|
| **核心定位** | 组织级常驻多角色 AI 团队协同 OS | 高精尖单 Agent 执行容器 | **保持 Nuke 优势**：继续巩固常驻团队与多角色群组协作壁垒。 |
| **工具调度** | 传统多轮 Tool Call (串行/交互式) | ⚡ **Code Mode (`run_code`)** 批量代码调度 | 🚀 **强烈吸收**：在 Worker 中引入代码调度模式，将 20 轮交互压缩至 1 轮。 |
| **防破坏门禁** | Hashline 局部原子替换 (防漂移) | 👁️ **Read-Before-Mutate** 强制先读后改 | 🚀 **强烈吸收**：在文件操作前置检查观察事实，双重杜绝幻觉盲改。 |
| **超限输出处理** | 自动脱敏 + Payload 归档 | 🌊 **Spill Policy** 溢出落盘 + 句柄切片定位 | 🚀 **强烈吸收**：防止长日志冲垮上下文窗口，提供按需切片读取。 |
| **扩展微内核** | 29-Type 事件总线 + DDD 六边形架构 | 🏛️ **Cordis 时空可组合微内核模式** | 🌟 **取其神不拘其形**：在 Python 原生实现洋葱圈中间件与 YAML 补丁。 |
| **长期认知记忆** | 🧠 **A-MEM 经历萃取 ➔ 常识反思** | ❌ 弱（仅限单 Session 事件回放） | **保持 Nuke 优势**：继续深化 A-MEM 认知复利与冲突消解。 |
| **多租户物理隔离** | 🏰 **每群专属独立 SQLite 数据库** | 🟡 目录级/配置级隔离 | **保持 Nuke 优势**：坚守企业多项目物理隔离安全红线。 |

---

## 🚀 3. 四大核心能力吸收与演进方案

### 🌟 吸收项 1：Code Mode（代码调度模式）落地

* **痛点**：传统 Tool Calling 面对“批量搜 20 个文件 ➔ 检查 ➔ 批量替换 ➔ 跑测试”的任务，需要 20 轮对话，消耗 50,000+ Token，耗时超过 60 秒。
* **演进设计**：
  1. 在 Worker 进程的 `tool_executor` 中引入 `run_code` 调度工具；
  2. 向 Coding Agent 注入强类型 SDK 声明（`declare const tools: { read, write, grep, bash }`）；
  3. 大模型直接生成包含 `for` 循环、`Promise.all`（或 Python `asyncio.gather`）和条件过滤的轻量脚本；
  4. 宿主机在本地隔离线程中毫秒级跑完，**只把提炼后的结构化结果（约 200 Token）返回大模型**。
* **预期收益**：Token 消耗降低 90%，多文件批量操作提速 10 倍。

---

### 🌟 吸收项 2：Read-Before-Mutate（先读后改观察门禁）

* **痛点**：大模型在未仔细阅读现有代码的情况下，容易凭空臆测已有逻辑，导致全量覆写造成代码破坏。
* **演进设计**：
  1. 在文件系统服务层记录每个文件的 `fs/observed` 事实链（记录读取时间与哈希）；
  2. 在执行 `edit`、`write`、`replace_file_content` 之前，强制走查该文件是否在本次任务中被读取过；
  3. 若未观察过，工具流水线直接抛出 `UnobservedFileMutationError` 拦截，强制 Agent 先读再改。

---

### 🌟 吸收项 3：Spill Policy（超大输出溢出暂存与句柄定位）

* **痛点**：终端测试日志或全文检索输出几万行数据时，若直接回传会冲垮上下文窗口并造成长链路注意力涣散。
* **演进设计**：
  1. 当工具输出超过阈值（如 50KB / 500 行）时，自动触发 Spill 机制；
  2. 将完整原始日志保存至 `workspaces/_spill/<spill_id>.log`；
  3. 给大模型返回前 50 行摘要 + 一个安全的本地定位句柄（`locator`）；
  4. Agent 后续可通过 `slice_read(locator, start_line, end_line)` 按需拉取关键片段。

---

### 🌟 吸收项 4：Cordis 架构设计模式的 Pythonic 原生实现

> **决策裁决**：**绝对不要直接引入 Cordis 的 Node.js 源码库**（避免破坏 Python 3.12+ 多进程拓扑），而是**在 Python 中原生实现其三大核心架构模式**：

1. **Pythonic Waterfall 洋葱圈责任链中间件**：
   - 将 Nuke 的 `tool_executor` 重构为支持洋葱圈模式的流水线：
     `权限审批 (HITL) ➔ 观察门禁 (Read-Before-Mutate) ➔ 底层执行 ➔ 结果脱敏 (Redaction) ➔ 溢出拦截 (Spill)`
   - 任何中间件均可调用 `next()` 穿透或随时**短路拦截**。
2. **声明式依赖注入与可逆销毁（IoC & Disposer）**：
   - 插件通过 `inject = ["db", "fs"]` 声明依赖，由 Worker 容器统一装配；
   - 插件卸载时自动反向注销注册的路由与事件监听，杜绝内存泄漏。
3. **声明式企业级 YAML 补丁机制（`nuke.patch.yml`）**：
   - 允许企业客户私有化部署时，零代码修改通过 YAML 覆盖存储层（如 SQLite ➔ PostgreSQL）或计算沙箱（如 Docker ➔ K8s Pod）。

---

## 🛡️ 4. 后台自主代码修改安全体系评估基线

通过本轮升级，Nuke 的后台代码修改安全体系将全面形成 **6 大闭环**：

```
================================================================================
🏛️ Nuke AI Collaborator 后台代码修改安全体系全景
--------------------------------------------------------------------------------
✅ 1. Git Worktree 物理隔离：    已闭环 (git_worktree.py · Promote/Discard)
✅ 2. 局部高精度原子修改：       已闭环 (editing/ · Hashline 行哈希防漂移)
✅ 3. 自动化测试与证据门禁：     已闭环 (Outcome Evidence · 测试未通过不入库)
✅ 4. 卡死检测与 Fenced Lease：  已闭环 (pipeline.py · 异步租约续期与熔断)
✅ 5. 凭据脱敏与超限保护：       已闭环 (redaction.py · 密钥/Token 自动消除)
✅ 6. PR Gate 准入门禁：         已闭环 (coding_agent.py · 缺失 PR 强制阻断)
================================================================================
```

---

## 🗺️ 5. 实施路线图 (Implementation Roadmap)

| 阶段 | 目标任务 | 交付物与检验标准 |
|:---|:---|:---|
| **Phase 1<br>(中间件化与门禁)** | • 重构 Tool Executor 为 Waterfall 洋葱圈架构<br>• 实现 Read-Before-Mutate 观察门禁与 Spill 溢出机制 | • 单元测试覆盖中间件短路拦截<br>• 未读文件覆写 100% 阻断测试 |
| **Phase 2<br>(Code Mode 接入)** | • 实现 Worker 端轻量代码调度沙箱 (`run_code`)<br>• 提示词动态注入 `tools` 强类型 SDK 声明 | • 复杂重构任务交互轮次从 15+ 轮压缩至 1~2 轮<br>• Token 消耗降低 80%+ |
| **Phase 3<br>(声明式补丁与配置)** | • 引入 `nuke.patch.yml` 声明式覆盖装配器<br>• 实现 Skill/Tool 插件卸载时的反向资源销毁 (Disposer) | • 支持免改代码一键切换底层存储/沙箱适配器 |

---

*本报告经架构评审确认，作为 Nuke AI Collaborator 后续版本演进与重构的权威技术依据。*
