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
| 授权记忆 | ✅ persist_rule + 子命令深度合成 | always-allow | — | — | ✅ 子命令深度感知合成 |
| 子 agent 权限 | ⚠️ 整体继承+深度上限 | Task 限工具集 | ✅ 衰减派生 | ✅ | （委托 CLI） |
| 参数校验 | ⚠️ 硬编码别名表 | ✅ zod validateInput | ✅ schema-decode | ✅ | （透传） |
| MCP 传输 | ⚠️ 仅 stdio | ✅ +WS* | ✅ +remote(SSE/HTTP)+OAuth | ✅ | （透传 CLI） |
| MCP 工具过滤 | allow_list | — | — | — | ✅ allow+block per-model |
| 工具规模治理 | ❌ 全量上传 | ✅ deferred+ToolSearch | — | ⚠️ searchable | （透传） |
| 工具输出脱敏 | ❌ 无 | — | — | ✅ result-middleware | — |
| 可观测 | ⚠️ trace_id，无 per-call span | ✅ span+attrs | ✅ | ✅ | — |

---

## 三、差距清单（按多 agent + 高频 shell + MCP 画像排序）

### 1. 🟡 命令安全为规则匹配（非 classifier 级）— 🔶 已加固
- **现状（已校正 + 加固）**：`_check_shell_command` 两层——① **编译正则** denylist（`_DANGEROUS_PATTERNS`，非子串；含 `base64 -d`、`curl|sh`、`eval $()`、fork bomb、写块设备等结构性模式）；② **tokenized 分析**（`_check_tokenized`：shlex 去引号/转义 → 剥 `VAR=val`/`sudo`/`env` 等 wrapper → basename 识别危险二进制，并递归 `bash -c "<cmd>"`）。后者堵住了正则的引号/空格/路径前缀/wrapper/命令链绕过（`rm -rf "/"`、`r''m -rf /`、`/usr/sbin/fdisk`、`env X=1 rm -rf "$HOME"`、`cd /tmp && rm -rf /`、`bash -c "rm -rf /"`）。单测 `tests/test_shell_guard.py`（53 例）。
- **残留（denylist 的本质上限，非本次目标）**：任意语言内联求值（`python -c "import os;os.system(...)"`）、运行期变量间接（`X=/; rm -rf $X`）、自写脚本再执行——静态 denylist 都不可能穷尽。**真正的一道闸是 HIL 权限门**（`_default_shell_guard` 无 ruleset fail-closed + `_permission_check_hook` ask/deny），denylist 仅为「即便规则放行也拦明显毁灭性命令」的 backstop。
- **对标 / 后续**：Claude Code `classifierApprovals.ts`（LLM `BASH_CLASSIFIER` + `isSearchOrReadCommand()` 只读命令自动放行）；openclaw 容器沙箱 + exec 边界 CodeQL。要再进一步需 classifier 级判定或容器/命名空间隔离——非规则匹配能覆盖，列为后续增强。

### 2. ✅ 工具输出密钥脱敏 — 已修复
- **现状（已实现）**：[executors/redaction.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/executors/redaction.py) `redact_secrets()` 按高置信格式脱敏(PEM 私钥块、JWT、AWS AKID、GitHub `ghp_`/`github_pat_`、OpenAI/Anthropic `sk-`、Slack `xox*`、Google `AIza*`、带凭据 URL→只遮密码、`Authorization: Bearer`、`*KEY/TOKEN/SECRET/PASSWORD/...=值`→保留键名只遮值)。在**两个**汇出口接入:① tool_executor after-hook `_default_secret_redactor`,**注册在截断器之前**(全文先脱敏再截断,截断持久化的内容也安全)——覆盖 builtin/run_shell/run_skill;② `McpClientToolProvider.execute`(MCP 走 router 不经 after-hook,自带一道)。与 MCP 结果围栏互补(那是防注入流入,这是防密钥流出)。
- **精度取舍**:高精度优先,只匹配可识别的密钥格式;不做通用高熵检测(否则会误遮 hash/ID/base64)。`PWD=`、`API_URL=` 等已验证不误遮。单测 `tests/test_redaction.py`(23 例)+ `test_mcp_provider.py::test_result_secrets_redacted`。
- **对标**:openclaw `tool-result-middleware.ts` 的 `AgentToolResultMiddleware` 链。
- **残留**:仅静态格式匹配,自定义/无格式密钥仍可能漏;通用高熵检测因误报率高未做。

### 3. 🟠 子 agent 权限只整体继承、不衰减
- **现状**：[tool_loop_v1.py:362-363](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/executors/plugins/tool_loop_v1.py) 子 agent `self.ruleset = self.ctx.ruleset`（spawn 时 [workspace_tools.py:223](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/executors/plugins/workspace_tools.py) 原样下传父 ruleset），护栏仅 `_SPAWN_MAX_DEPTH` 深度上限。子 = 父的全部权限，无最小权限子集。
- **对标**：opencode `src/agent/subagent-permissions.ts` 的 `deriveSubagentSessionPermission`：子 agent 拿到的是父的 deny 子集 + 父 agent 的 edit-deny，并把 `task`/`todowrite` 默认 deny（除非子 agent 显式允许）。
- **建议**：派生时收敛为「父权限 ∩ 子任务所需」，深层默认收紧 spawn / write。

### 4. 🟠 工具规模治理缺失（无动态加载）
- **现状**：[tool_router.py `get_external_schemas()`](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/executors/tool_router.py) + [tool_loop_v1.py 的 schema 合并](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/executors/plugins/tool_loop_v1.py) 把工具 schema 全量上传给 LLM；MCP 工具一多即膨胀 context。
- **对标**：Claude Code `src/utils/toolSearch.ts`——deferred 工具以 `defer_loading: true` 发送，经 `ToolSearchTool` 按需发现，并有 token 预算（`countToolDefinitionTokens`）。（即本对话所在 harness 正在用的机制。）
- **建议**：对 MCP / 低频工具做 deferred 加载 + 工具检索，按 token 预算装配。

### 5. 🟠 MCP 仅 stdio，无 remote / OAuth
- **现状**：[mcp_client.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/executors/providers/mcp_client.py) 仅 `StdioServerParameters`；有 `allow_list` / `call_timeout` / 单 task 会话。✅ **自动重连已修复**（`4d666be`）：`execute()` 入口调 `_ensure_alive()`，会话 task 已死时尝试一次重连，由 `_reconnect_lock` 串行化（防重连风暴）+ `_RECONNECT_COOLDOWN=5s` 冷却（防持续锤击死服务）。真实未解决：**无 remote 传输**（接不了纯网络 API 工具生态）、**无 OAuth**（无授权）、**无 `ToolListChanged` 订阅**（server 增删工具不感知）。
- **对标**：opencode `src/config/mcp.ts` 支持 Local + Remote（SSE/HTTP）+ OAuth（含 RFC 7591 动态注册）+ 每服务器 timeout；Claude Code 另带 `mcpWebSocketTransport.ts`*。
- **建议**：加 remote(SSE/HTTP) 传输 + 授权 + `ToolListChanged` 订阅。

### 6. ✅ 授权记忆粒度（persist_rule）— 已修复（曾被低估为 🟠，实为安全问题）
- **原问题**：`engine.py` 的 "always" 持久化为 `Rule(tool_pattern=tool_name, args_pattern="")`——**空 args_pattern = 放行该工具的全部调用**。即"始终允许 `git status`"会连带自动放行 `rm -rf /`。这不只是"不智能",是安全洞。
- **现状（已实现）**：新增 [permissions/patterns.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/permissions/patterns.py) `synthesize_args_pattern()`，`engine.check` 在 always 分支据此合成**有粒度**的 pattern：
  - run_shell：shlex 分词 → 剥 `VAR=val`/`sudo`/`env` → 按**子命令深度表**取识别前缀（`git push origin main`→`git push *`；`ls -la`→`ls *`；`docker compose up`→`docker compose *`；纯 `pwd`→精确 `pwd`），尾部 `" *"` 保 token 边界(`ls` 不会扩成 `lsof`)，glob 元字符转义。
  - 路径类(write_file/read_local_file/write_local_file)→精确路径；spawn_agent→精确 bot_name；其余→空(回退)。
  - 单测 `tests/test_permission_patterns.py`(16 例，含"`git push *` 规则不放行 `rm -rf /`")。
- **残留**：服务端默认合成，未做 gsd-2 那种"让用户在 `Bash(gh:*)`/`Bash(gh pr:*)` 间选粒度"的前端菜单——需 WS 协议 + 前端改动，列为后续体验增强（功能性安全洞已堵）。
- **对标**：gsd-2 `buildBashPermissionPattern`（子命令深度）+ `buildBashPermissionPatternOptions`（粒度菜单）。
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
