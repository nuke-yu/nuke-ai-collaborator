# 工具执行层横向对比与差距分析

> 最后更新：2026-06-07
> 状态：分析报告
> 关联文档：[TOOL-EXECUTOR-REFACTOR-DESIGN.md](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/docs/TOOL-EXECUTOR-REFACTOR-DESIGN.md)、[TOOL-ROUTER-STRATEGIC-SOLUTION.md](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/docs/TOOL-ROUTER-STRATEGIC-SOLUTION.md)、[MCP-SYSTEM-COMPARISON.md](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/docs/MCP-SYSTEM-COMPARISON.md)

本文把 nuke 的工具执行层（`tool_executor` / `tool_router` / `permissions` / `workspace_tools`）与四套本地可读源码的系统横向对比，定位差距并给出可落地建议。

---

## 一、对比对象与方法

| 系统 | 路径 | 读取范围 | 形态 |
| :--- | :--- | :--- | :--- |
| **nuke**（本项目） | `backend/executors/` | 全量 | 自有工具执行引擎 |
| **Claude Code**（haha 修复版，泄露源码） | `/Users/Nuke/claude-code-haha-main` | classifierApprovals / permissions / Tool / toolSearch | 自有引擎（金标准） |
| **opencode** | `/Users/Nuke/opencode/packages/opencode` | tool / permission / subagent / mcp 配置 | Effect 服务池 |
| **openclaw** | `/Users/Nuke/openclaw-main` | tool-result-middleware / CodeQL / sandbox 脚本 | 自有引擎 + 容器沙箱 |
| **gsd-2** | `/Users/Nuke/gsd-2` | stream-adapter（权限回调/bash pattern）/ mcp-filter | 编排层（委托 Claude Code CLI 执行） |

> 诚实声明：除一处明确标注外，下列结论均来自实际读取源码。唯一例外——Claude Code 的 WebSocket MCP transport 仅读到文件名 `src/utils/mcpWebSocketTransport.ts` 与用途，未读内部实现，文中以 `*` 标注。

---

## 二、五方能力矩阵

| 能力 | nuke | Claude Code | opencode | openclaw | gsd-2 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 工具执行 | 自有引擎 | 自有引擎 | Effect 服务 | 自有+容器 | 委托 CLI |
| 命令安全决策 | ⚠️ 子串匹配 | ✅ 分类器 | 权限规则 | ✅ 容器+CodeQL | ✅ pattern 合成+粒度菜单 |
| 权限裁决契约 | ⚠️ block/allow | ✅ 模式+多源分层 | ✅ ask/allow/deny+pattern | ✅ | ✅ 改参/改权限/中断 |
| 授权记忆 | persist_rule（原样） | always-allow | — | — | ✅ 子命令深度感知合成 |
| 子 agent 权限 | ⚠️ 整体继承+深度上限 | Task 限工具集 | ✅ 衰减派生 | ✅ | （委托 CLI） |
| 参数校验 | ⚠️ 硬编码别名表 | ✅ zod validateInput | ✅ schema-decode | ✅ | （透传） |
| MCP 传输 | ⚠️ 仅 stdio | ✅ +WS* | ✅ +remote(SSE/HTTP)+OAuth | ✅ | （透传 CLI） |
| MCP 工具过滤 | allow_list | — | — | — | ✅ allow+block per-model |
| 工具规模治理 | ❌ 全量上传 | ✅ deferred+ToolSearch | — | ⚠️ searchable | （透传） |
| 工具输出脱敏 | ❌ 无 | — | — | ✅ result-middleware | — |
| 可观测 | ⚠️ trace_id，无 per-call span | ✅ span+attrs | ✅ | ✅ | — |

---

## 三、差距清单（按多 agent + 高频 shell + MCP 画像排序）

### 1. 🔴 命令安全只靠子串匹配
- **现状**：[workspace_tools.py:454-470](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/executors/plugins/workspace_tools.py) 的 `_default_shell_guard` 用 `pattern.lower() in cmd` 对 `_DANGEROUS_PATTERNS` 做子串匹配；base64 / 变量拼接 / 脚本文件体一律绕过。
- **对标**：Claude Code `src/utils/classifierApprovals.ts` 用**分类器**（`BASH_CLASSIFIER` 判断 bash 命令是否可自动放行、`TRANSCRIPT_CLASSIFIER` 读 transcript 决定 auto 模式）；`src/Tool.ts` 的 `isSearchOrReadCommand()` 把读/搜命令归类自动放行。openclaw 直接上**容器沙箱** + 对 exec 边界做 CodeQL 高危扫描（`.github/codeql/codeql-mcp-process-tool-boundary-critical-security.yml`）。
- **建议**：命令安全决策从子串升级到分类器（或至少 token/AST 级解析）；高危执行考虑容器/命名空间隔离。

### 2. 🔴 工具输出无密钥脱敏
- **现状**：`executors/` 下无任何 redact/脱敏逻辑（全量 grep 零命中）；after-hook 只做截断。`run_shell` 打印的 env / token 直接进模型上下文，并会注回其他 agent。
- **对标**：openclaw `extensions/tokenjuice/tool-result-middleware.ts` 提供 `AgentToolResultMiddleware` 链，对工具结果做脱敏。
- **建议**：在 after-hook（或未来 router 管线）加一道输出脱敏中间件。多 agent 共享上下文使泄漏放大，优先级高。

### 3. 🟠 子 agent 权限只整体继承、不衰减
- **现状**：[tool_loop_v1.py:362-363](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/executors/plugins/tool_loop_v1.py) 子 agent `self.ruleset = self.ctx.ruleset`（spawn 时 [workspace_tools.py:223](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/executors/plugins/workspace_tools.py) 原样下传父 ruleset），护栏仅 `_SPAWN_MAX_DEPTH` 深度上限。子 = 父的全部权限，无最小权限子集。
- **对标**：opencode `src/agent/subagent-permissions.ts` 的 `deriveSubagentSessionPermission`：子 agent 拿到的是父的 deny 子集 + 父 agent 的 edit-deny，并把 `task`/`todowrite` 默认 deny（除非子 agent 显式允许）。
- **建议**：派生时收敛为「父权限 ∩ 子任务所需」，深层默认收紧 spawn / write。

### 4. 🟠 工具规模治理缺失（无动态加载）
- **现状**：[tool_router.py `get_external_schemas()`](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/executors/tool_router.py) + [tool_loop_v1.py 的 schema 合并](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/executors/plugins/tool_loop_v1.py) 把工具 schema 全量上传给 LLM；MCP 工具一多即膨胀 context。
- **对标**：Claude Code `src/utils/toolSearch.ts`——deferred 工具以 `defer_loading: true` 发送，经 `ToolSearchTool` 按需发现，并有 token 预算（`countToolDefinitionTokens`）。（即本对话所在 harness 正在用的机制。）
- **建议**：对 MCP / 低频工具做 deferred 加载 + 工具检索，按 token 预算装配。

### 5. 🟠 MCP 仅 stdio，无 remote / OAuth / 健康检查
- **现状**：[mcp_client.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/executors/providers/mcp_client.py) 仅 `StdioServerParameters`；有 `allow_list` / `call_timeout` / 单 task 会话，但无 remote 传输、无 OAuth、子进程死后「re-init 由调用方负责」无自动重连。
- **对标**：opencode `src/config/mcp.ts` 支持 Local + Remote（SSE/HTTP）+ OAuth（含 RFC 7591 动态注册）+ 每服务器 timeout；Claude Code 另带 `mcpWebSocketTransport.ts`*。
- **建议**：加 remote(SSE/HTTP) 传输 + 鉴权 + 健康检查/重连。

### 6. 🟠 授权记忆太粗（persist_rule 不智能）
- **现状**：[workspace_tools.py:513-518](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/executors/plugins/workspace_tools.py) `persist_rule` 存 `tool_pattern` / `args_pattern`，但 pattern 合成不智能。
- **对标**：gsd-2 `src/resources/extensions/claude-code-cli/stream-adapter.ts` 的 `buildBashPermissionPattern`——命令链提取（`cd /foo && gh pr list` 取有意义段）、剥 `sudo/env/VAR=`、**按子命令深度**捕获（`git push:*` 深 1、`gh pr create:*` 深 2、`aws/az/gcloud` 深 2）；`buildBashPermissionPatternOptions` 给用户 `Bash(gh:*)` / `Bash(gh pr:*)` / `Bash(gh pr list:*)` 粒度菜单。
- **建议**：抄 gsd-2 的子命令深度表 + 链式提取，让「始终允许」生成精准、可选作用域的规则。多 agent 高频跑 git/gh/npm 收益大。

### 7. 🟡 权限裁决契约偏窄
- **现状**：before-hook 只能返回 `{block, reason}`（[tool_executor.py:171-181](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/executors/tool_executor.py)），不能改写入参或动态授权。
- **对标**：gsd-2 `CanUseToolPermissionResult`（stream-adapter.ts:774）支持 `allow + updatedInput + updatedPermissions` 与 `deny + interrupt`；Claude Code 有 plan/acceptEdits/bypass/dontAsk/auto 等**模式**与 user/project/local/**policy**/cli/session **多来源分层规则**（`src/types/permissions.ts`）。
- **建议**：裁决结果支持「改写入参（如自动修正路径）/ 动态授予 / 中断」；权限规则引入多来源分层（尤其管理员 policy 层）与 plan/acceptEdits 模式。

### 8. 🟡 参数校验靠硬编码别名表
- **现状**：[tool_executor.py:17-22](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/executors/tool_executor.py) `_ARG_ALIASES` 仅覆盖 `file_path/contents` 等少数；表外近似参数 → handler 抛 `unexpected kwarg` → `[执行错误]`，模型拿不到结构化纠错。
- **对标**：Claude Code 每工具 zod `validateInput()`（`src/Tool.ts`）；opencode schema-decode 失败抛类型化 `InvalidArgumentsError` 并给模型「请按 schema 重写输入」。
- **建议**：对每个工具的 parameters 做真校验 + 标准化纠错回馈。

### 9. 🟡 MCP 工具只能 allow，缺按上下文 block 过滤
- **现状**：`mcp_client.py` 仅 provider 级 `allow_list`。
- **对标**：gsd-2 `src/resources/extensions/gsd/mcp-filter.ts` 的 `computeMcpDisallowedTools`：扫 `.mcp.json` + `.claude/settings.json` 发现 server，按 model 算 allowlist + blocklist 两段，产出 `mcp__{name}__*` 禁用模式。
- **建议**：支持按 bot / 场景 / 模型动态裁剪可见 MCP 工具集（黑白名单两段）。

---

## 四、nuke 已具备的优点（对标后仍在水准线上）

- **MCP 单 task 会话所有权**：[mcp_client.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/executors/providers/mcp_client.py) 用专属 task 持有 stdio_client + ClientSession，规避 anyio 跨 task cancel-scope 错误——与 opencode 同级。
- **HIL 权限 + persist_rule 记忆**：`permissions.check` 支持 ask/deny/allow + 规则持久化。
- **端口拦截 / 动态分配**：[workspace_tools.py 的 `_INTERCEPT_PORTS` / `_allocate_free_port`](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/executors/plugins/workspace_tools.py)（对标几套均未见）。
- **DFT-022 禁 skill 内嵌 shell**：[skills/processor.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/skills/processor.py) 关闭自写 skill → RCE 路径，比多数框架更严。
- **trace_id 全链路传递**：[runtime/tracing.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/runtime/tracing.py)（缺的是 per-call span，不是无追踪）。
- **spawn 深度上限 + `concurrency_safe` 并行批处理**。

---

## 五、各框架可借鉴点速查

| 来源 | 可直接借鉴 |
| :--- | :--- |
| Claude Code | 分类器审批；deferred 工具加载 + ToolSearch；zod 参数校验；权限模式 + 多源分层规则 |
| opencode | 子 agent 权限衰减派生；MCP remote + OAuth + per-server timeout |
| openclaw | 工具结果脱敏中间件链；容器沙箱；exec 边界 CodeQL 安全扫描 |
| gsd-2 | bash 授权 pattern 智能合成（子命令深度）；授权粒度菜单；MCP per-model 黑白名单过滤；canUseTool 改参/中断契约 |

---

## 六、优先级建议

1. **先做（安全收益最高，多 agent 放大风险）**：#1 命令安全分类器、#2 输出脱敏、#3 子 agent 权限衰减。
2. **再做（多 MCP / 规模化前置）**：#4 deferred 工具加载、#5 MCP remote+健康检查、#9 MCP 上下文过滤。
3. **体验/健壮性**：#6 智能授权记忆、#7 裁决契约扩展、#8 参数校验。
