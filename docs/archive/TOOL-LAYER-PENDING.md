# 工具执行层 / MCP —— 未处理项 (Pending Backlog)

> 最后更新：2026-06-08
> 关联：[TOOL-LAYER-GAP-ANALYSIS.md](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/docs/TOOL-LAYER-GAP-ANALYSIS.md)、[MCP-SYSTEM-COMPARISON.md](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/docs/MCP-SYSTEM-COMPARISON.md)、[TOOL-ROUTER-STRATEGIC-SOLUTION.md](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/docs/TOOL-ROUTER-STRATEGIC-SOLUTION.md)

本文统一登记工具执行层 / MCP 工作线中**已知但刻意未做 / 待外部条件**的项。多数是 speculative
generality（给可信内部工具加当前无消费方的能力）或需外部依赖才能验证的，按"有真实驱动再做"。

已完成的项不在此（见 GAP 分析的 ✅ 条目）。

---

## A. 刻意缓做（YAGNI — 当前无消费方/无触发条件）

| # | 项 | 为何缓做 | 何时该做（触发条件） |
| :-- | :--- | :--- | :--- |
| P1 | **裁决契约扩展**（GAP #7）：before-hook/permission 结果支持「改写入参 / 动态授予 / 中断 / 多源分层（admin policy 层）」 | 现无任何 hook/规则需要改参或中断；多源分层无实际场景在等。做了是空接口 + 维护负担。 | 出现真实需求：要 admin 全局 deny 策略、或要自动修正/改写工具入参、或要"拒绝并中断整个 agent 循环"。 |
| P2 | **完整 dynamic ToolSearch**（GAP #4）：deferred 工具按需检索 + 运行期装载 | 重造 Anthropic `defer_loading` 平台能力；当前 0–1 个 MCP server 远不触及 48 schema 预算上限（已有预算护栏防 context 爆炸）。 | 接入 `defer_loading` API（顺平台做），或真出现大量 MCP 工具/server 长期超预算。 |
| P3 | **命令安全 classifier 级 / 容器隔离**（GAP #1 残留）：现为「正则 + tokenized denylist」backstop，HIL 权限门为主闸 | denylist 本质无法穷尽：任意语言内联求值（`python -c "os.system(...)"`）、运行期变量间接（`X=/; rm -rf $X`）、自写脚本再执行。再进一步需 classifier 判定或容器/命名空间隔离。 | 威胁模型升级（接不可信外部代码/agent），或要 auto-approve 只读命令（Claude Code `isSearchOrReadCommand` 式）。 |

---

## B. 待外部依赖/条件才能完成或验证

| # | 项 | 现状 | 缺什么 |
| :-- | :--- | :--- | :--- |
| P4 | **MCP OAuth 端到端握手验证** | 代码完整（McpAuthTool 式 + client_id/secret 透传 + token 持久化）；总线/存储/流程接缝已单测；stdio+PAT 已真机验证。 | 一个**真实远程 OAuth MCP server**（如 `api.githubcopilot.com/mcp` + GitHub OAuth App）+ 浏览器交互，跑一遍 `mcp_authenticate` → 授权 → 回调 → tools 加载。步骤见 MCP-SYSTEM-COMPARISON §六.3。 |
| P5 | **MCP 独立健康检查** | 靠会话级自动重连兜底（崩溃→下次调用重连）。 | 主动健康探测（周期 ping / 状态上报），而非被动重连。低优。 |

---

## C. 各已完成项的小残留（按需再加，非阻塞）

- **参数校验**（#8）：未做嵌套对象/数组元素深校验、enum / 数值范围校验。
- **MCP 工具过滤**（#9）：per-bot 已做；未做 gsd-2 式 **per-model** 维度；为**可见性**过滤（LLM 看不到即不调），未做执行期硬阻断（与现有 `allowed_tools` 模型一致）。
- **输出脱敏**（#2）：仅高置信格式匹配；自定义/无格式密钥可能漏；通用高熵检测因误报率高未做。
- **子 agent 权限衰减**（#3）：未做"按子任务声明再收紧"或逐层递减（刻意不过度收紧以免卡多级协作）。
- **MCP 进程回收**：已做 shutdown 时进程树强杀；per-provider 重连路径的孙进程残留未单独清（重连罕见，shutdown 兜底）。
- **可观测**：有 `trace_id` 全链路；无 per-call span / attrs。
- ~~**MCP proxy 查找**~~ → ✅ 已做：`MCPBridge.set_schemas` 维护 name→schema dict，`schema_for()` O(1)，`can_handle`/`_needs_approval` 改用它。
- ~~**OAuth token 存储连接**~~ → ✅ 已做：`mcp_oauth_store` 改为**按 db_path 共享一条惰性 aiosqlite 连接**（collector 单进程单 loop → 全程复用，消除 per-op 连接/线程抖动；aiosqlite 在单连接上串行化操作，并发刷新安全）。`aclose_all()` 在 collector 退出 + 测试 teardown 关闭；按 path 键避免跨 event-loop 复用（测试用唯一临时 db）。

---

## D. 架构演进（北极星，已有专文）

- **Plan B：统一 ToolProvider 路由**（hooks 进 router 本体、Shell/Skill provider 接入）——见 [TOOL-ROUTER-STRATEGIC-SOLUTION.md](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/docs/TOOL-ROUTER-STRATEGIC-SOLUTION.md)。当前 Plan A 双车道，迁移判据与硬约束见该文 §五/§六。
