# 工具执行层横向对比与差距分析

> 最后更新：2026-06-08
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
| 命令安全决策 | 🔶 正则+tokenized（非 classifier） | ✅ 分类器 | 权限规则 | ✅ 容器+CodeQL | ✅ pattern 合成+粒度菜单 |
| 权限裁决契约 | ⚠️ block/allow | ✅ 模式+多源分层 | ✅ ask/allow/deny+pattern | ✅ | ✅ 改参/改权限/中断 |
| 授权记忆 | ✅ persist_rule + 子命令深度合成 | always-allow | — | — | ✅ 子命令深度感知合成 |
| 子 agent 权限 | ✅ 衰减派生(去 bypass+去高危 blanket+深度上限+不可弹窗) | Task 限工具集 | ✅ 衰减派生 | ✅ | （委托 CLI） |
| 参数校验 | ✅ 别名归一 + schema 必填/类型校验 | ✅ zod validateInput | ✅ schema-decode | ✅ | （透传） |
| MCP 传输 | ✅ stdio + remote(SSE/HTTP) + OAuth（collector 进程） | ✅ +WS* | ✅ +remote(SSE/HTTP)+OAuth | ✅ | （透传 CLI） |
| MCP 工具过滤 | ✅ allow_list + per-bot allow/block | — | — | — | ✅ allow+block per-model |
| 工具规模治理 | 🔶 schema 数量预算（无 deferred 检索） | ✅ deferred+ToolSearch | — | ⚠️ searchable | （透传） |
| 工具输出脱敏 | ✅ redact_secrets | — | — | ✅ result-middleware | — |
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

### 3. ✅ 子 agent 权限衰减 — 已修复
- **现状（已实现）**：spawn 不再原样下传父 ruleset。[workspace_tools._spawn_agent_handler](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/executors/plugins/workspace_tools.py) 改为 `ruleset=permissions.derive_subagent_ruleset(parent)`（[engine.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/permissions/engine.py)）。衰减策略(按本项目「可信内部工具 → 收敛爆炸半径」校准，非防对抗)：
  - **bypassPermissions 不传递** → 子降为 `default`（bypass 父不会把无门禁的递归 shell/file 权交给子）。
  - **deny 规则全部保留**(严格继承)。
  - **空 args_pattern 的高危 ALLOW 丢弃**（`run_shell`/`write*`/`spawn_agent` 的"放行全部"、或 `*` 通配 allow）——子不继承"放行所有 shell";**有粒度的 allow(`git push *`)和低危 allow 保留**,正常预授权照常流转。
  - 叠加既有约束:子 agent **无法弹窗**(engine `spawn_depth>0` 时 ask→deny)+ `_SPAWN_MAX_DEPTH` 深度上限。三者合起来 = 子权限收敛为「父的 deny + 父的有粒度/低危 allow」,且不可自行升权。
- **残留 / 取舍**:未做 opencode 那种"按子任务声明再收紧"或逐层递减;按用户 steer 刻意不过度收紧以免卡正常多级协作。单测 `tests/test_subagent_perms.py`(11 例)。
- **对标**:opencode `deriveSubagentSessionPermission`(deny 子集 + task/todowrite 默认 deny)。

### 4. 🔶 工具规模治理 — 已加预算上限（完整 deferred 检索为后续）
- **现状（已实现预算）**：[tool_loop_v1._apply_external_schema_budget](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/executors/plugins/tool_loop_v1.py) 对外部(MCP) schema 设上限 `_MAX_EXTERNAL_TOOL_SCHEMAS=48`：builtin 永不裁,超额 MCP 工具不下发并在 system prompt 注一条"另有 N 个工具未加载,请用 allow_list 收窄"的提示 + 告警。直接堵住"MCP 工具一多 context 膨胀"。
- **残留**：超额工具暂不可调用(未做 Claude Code 那种 `ToolSearchTool` 动态检索+按需装载);当前 0-1 个 server 远未触限,属预防性护栏。配合每 server `allow_list` 收窄是主手段。
- **对标**：Claude Code `toolSearch.ts`(deferred + `countToolDefinitionTokens` token 预算)。

### 5. ✅ MCP remote + OAuth — 已实现（运行于 mcp-collector 进程）
- **架构**：MCP 已迁到独立 **mcp-collector 进程**(跨群组单例,supervisor 作 bus);worker 经 `McpProxyProvider`+bridge 转发,collector 独占连接 + OAuth + 脱敏/围栏;权限留 worker。见 [mcp_collector.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/runtime/mcp_collector.py) / [mcp_proxy.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/executors/providers/mcp_proxy.py) / [mcp_bridge.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/executors/mcp_bridge.py)。
- **传输**：stdio + remote SSE + remote streamable-HTTP(`_open_transport` 按 `url`/`transport` 选,`headers` 带鉴权);自动重连;`ToolListChanged` 订阅 → schema re-push 到 worker。
- **OAuth（McpAuthTool 式，已实现）**：SDK `OAuthClientProvider`(PKCE/RFC 7591 动态注册/刷新);`mcp_authenticate(server)` 工具 → 授权 URL 进聊天 → 用户授权 → main `/mcp/oauth/callback` 经 bus 回 collector 完成 → tools 加载。token 存 [mcp_oauth.db](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/executors/providers/mcp_oauth_store.py);回调基址 env `PUBLIC_BASE_URL`(默认 `http://127.0.0.1:8000`);有 token 启动自动连、无 token 按需。
- **残留**：独立健康检查(靠重连兜底)、进程树强杀;**OAuth 端到端握手需真实 server 验**(总线/存储/流程接缝已单测)。
- **单测**:`test_mcp_provider`/`test_mcp_collector`/`test_mcp_proxy`/`test_supervisor_mcp_relay`/`test_mcp_auth_flows`/`test_mcp_oauth_store`。

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

### 8. ✅ 参数校验 — 已实现（别名归一 + schema 校验）
- **现状**：别名归一(`_ARG_ALIASES`)之后,`tool_executor.execute` 调 [`_validate_arguments`](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/executors/tool_executor.py) 按工具 JSON schema 校验:**缺必填参数**、**标量类型错**(含 bool 不当 integer)→ 返回**结构化中文错误**(`[参数错误] 缺少必填参数 'x'；参数 'y' 应为 integer…`)供 LLM 自纠,handler 不执行。
- **取舍**：刻意**轻量、零误拒**——不做 additionalProperties/format 校验(避免误杀合法调用),空 schema 即 no-op。比 jsonschema 全量更可预测。单测 `tests/test_arg_validation.py`(8 例)。
- **对标**：Claude Code zod `validateInput()`;opencode schema-decode → `InvalidArgumentsError`。
- **残留**：未做嵌套对象/数组元素的深校验、enum/范围校验——按需再加。

### 9. ✅ MCP 工具按 bot 黑白名单过滤 — 已实现
- **现状**：两段过滤。① **provider 级 `allow_list`**(collector 侧,server 注册哪些工具);② **per-bot 可见性**([tool_loop_v1._filter_mcp_schemas](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/executors/plugins/tool_loop_v1.py)):bot 的 `executor_config.mcp = {"allow":[...], "block":[...]}`,fnmatch glob 匹配 `{server}__{tool}`(如 `github__*` / `github__create_issue` / `*`),block 优先、allow 为白名单。这样可"只让 dev bot 看到 github MCP"。
- **取舍 / 残留**:与 `allowed_tools` 同为**可见性**过滤(LLM 看不到即不会调);未做执行期硬阻断(与现有 allowed_tools 模型一致)、未做 gsd-2 那种 **per-model** 维度(按需再加)。单测 `tests/test_tool_schema_budget.py::TestFilterMcpSchemas`(5 例)。
- **对标**：gsd-2 `mcp-filter.ts` `computeMcpDisallowedTools`(per-model allow/block 两段)。

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

1. **✅ 已完成（安全收益最高，多 agent 放大风险）**：#1 命令安全（tokenized 加固）、#2 输出脱敏、#3 子 agent 权限衰减、#6 授权记忆粒度。
2. **多 MCP / 规模化**：✅#4 schema 数量预算（完整 deferred 检索待做）、✅#5 MCP remote+ToolListChanged+OAuth 已做（独立健康检查/进程树强杀待做，OAuth 握手需真实 server 验）；✅#9 MCP 按 bot 黑白名单过滤已做（per-model 维度待按需）。
3. **体验/健壮性**：✅#8 参数校验已做；待做 #7 裁决契约扩展（改参/动态授权/中断）。
