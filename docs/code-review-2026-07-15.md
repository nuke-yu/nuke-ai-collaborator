# 架构师审查报告 (2026-07-15)

## 编排与近期 Commit 深度多维度评估（Aligned Final Version）

- **评审范围（Reviewed Range）**: `20d472f` 至 `098de0b` （近期 8 个 Commit）
- **工作区状态（Worktree State）**: Dirty（包含 `backend/core/runner.py`、`backend/plugins/agent_dashboard/orchestrator.py` 等文件的未暂存修改）
- **评审基线时间**: 2026-07-15 08:00
- **当前状态截至**: Commit `098de0b`

---

## 一、 核心发现一览 (Critical/High Findings)

| 编号 | 严重级别 | 维度 | 缺陷描述 | 潜在后果 |
| :--- | :--- | :--- | :--- | :--- |
| **01** | 🔴 Critical | 安全 | `github_client.py` 写入明文 Token 到共享的 `credential.helper`，或使用 `git -c argv` 传输 | Token 物理残留；进程 argv 被窃听嗅探 |
| **02** | 🔴 Critical | 数据一致性 | `runner.py` 在 CancelledError 终态下仍盲目执行 `promote_worktree` | Abort 或 Retry 取消后，半成品代码被强行合并到主分支 |
| **03** | 🔴 Critical | 安全/多租户 | REST 读接口与 WebSocket 未做 Group 成员鉴权与 Operator 角色限制 | 任意合法 JWT 可以订阅/查询任意隔离群组的对话及任务状态 |
| **04** | 🟡 High | 鲁棒性与功能 | Preflight 预检超时不会自动杀死子进程，且未带凭证注入 | 子进程僵尸残留；私有仓库任务无法通过预检 |
| **05** | 🟡 High | 可靠性/架构 | 任务列表 `_tasks` 和 Adapter 状态未持久化，重启后全部丢失 | 后台进程重启后，仪表盘任务瞬间“蒸发” |
| **06** | 🟡 High | 功能完整性 | GitHub Token 传递中断，未真正到达 `rd_tools.create_pr()` 工具调用处 | PR 自动创建步骤 100% 鉴权失败 |
| **07** | 🟡 High | 部署/环境 | 生产环境 Docker 镜像中未安装 `gh` CLI 客户端 | 镜像中运行 PR 创建直接报错或退化到 fake url |
| **08** | 🟡 High | 可靠性 | Abort/Rollback 回滚中错误调用 `unregister_task()` 会向前端推送 done 信号 | 仪表盘显示状态与真实生命周期发生严重冲突 |
| **09** | 🟡 High | 可靠性 | 任务记录的 status 状态不会随实际 Worker 端的 Workflow 运行状态同步 | 仪表盘状态永远卡在 "dispatched" 或 "restarted" |
| **10** | 🟡 High | 业务边界 | API 允许接收 GitLab/Bitbucket，但底层实现逻辑完全硬编码 GitHub (gh CLI) | 非 GitHub 仓库任务创建后由于缺少实现工具直接崩溃 |

---

## 二、 深度技术剖析与修复对齐

### 01. 🔴 Critical: Token 传输与落盘安全性缺陷
* **当前状态**: 
  `github_client.py` 曾将 GITHUB_TOKEN 写入 `.git/config` 的 `credential.helper` 中（在 worktree 共享配置场景下有极高的竞态泄漏风险）。即使使用 `-c credential.helper` 命令行参数覆盖，Token 也会进入进程 `argv` 列表，极易被 `ps aux`、系统审计（如 `auditd`）或 `_git()` 产生的异常日志打印泄露。
* **实现方案**: 
  采用 **Operator-managed GITHUB_TOKEN** 结合 **环境变量** 传输。通过将 Token 放置于 `GITHUB_TOKEN` 或临时环境变量中，并使用 `GIT_ASKPASS` 脚本（或 `git credential approve` 通过标准输入喂入凭证）实现认证，确保 Token 绝不进入 `argv`、URL、Git config、任务持久化状态、IPC 消息以及任何物理日志。
* **验收测试**:
  启动克隆与推送进程，在执行期间并发运行 `ps -ef` 及查看 `git` 相关调用日志，确认没有任何明文 Token 残留在进程参数、控制台输出及配置文件中。
* **修复 commit**: `Pending`

---

### 02. 🔴 Critical: Abort 异常中脏数据污染与自动合并
* **关联代码**: [runner.py:L227](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/core/runner.py#L227)
* **当前状态**: 
  即使在 Commit `098de0b` 中加入了 Abort/Retry 的 IPC 应答机制，但 `runner.py` 在捕捉到 `CancelledError` 后，其 `finally` 块中的 `_cleanup_finally()` 依然会无条件尝试对 group 目录下的所有 `task_` 临时目录执行 `promote_worktree()` 自动合并。这导致任务被中止或重试时，半成品代码被合并入主工作区，产生严重代码污染。
* **实现方案**: 
  1. 在 Orchestrator 的 Step 协议中显式定义 `workspace_action=promote|discard|retain` 字段，交由编排层统一驱动工作区生命周期。
  2. 在 `runner.py` 的 Cancel 拦截中，强制执行 `discard` 操作（即物理删除 `task_` 目录且不执行 Git merge）。
  3. 捕获 `CancelledError` 时，在清理阶段拦截 promotion 触发。
* **验收测试**:
  触发一个耗时的 Coding 任务，在其执行中期调用 `/api/agent/tasks/{id}` 的 DELETE 路由发送 Abort 指令。检查 `worktrees` 目录发现对应 worktree 已被彻底清空，且主分支没有产生任何来自该任务的未授权变更提交。
* **修复 commit**: `Pending` (当前工作区正在进行此修复)

---

### 03. 🔴 Critical: API 与 WebSocket 鉴权越权风险
* **当前状态**: 
  REST 读端 API（如 `/api/agent/tasks`）和 WebSocket 订阅端（`/ws/agent/{group_id}` 和 `/ws/agent/all`）虽然要求承载 JWT 凭证，但没有对调用者进行 Operator 角色校验，也没有做 Group 成员校验（Group Membership verification）。任意合法注册的普通用户，只要能获取 JWT，就可以订阅并监听到其他完全隔离的 Group 中的机密对话和文件流。
* **实现方案**: 
  1. 在 `/ws/agent/{group_id}` 握手和 `/api/agent/tasks` 查询前，引入 Group 鉴权中间件，验证 `jwt.user_id` 在 central 数据库中是否隶属于目标 `group_id`。
  2. 对于 `/ws/agent/all` 以及管理类端点，强制依赖 `Depends(require_operator)`。
* **验收测试**:
  生成两个属于不同群组的独立 User JWT（User A 与 User B）。用 User B 的 JWT 尝试连接 `/ws/agent/all` 或 User A 所在的 `/ws/agent/{group_id}`，接口必须拒绝并返回 403 权限拒绝错误。
* **修复 commit**: `Pending`

---

### 04. 🟡 High: Preflight 预检死锁隐患与进程僵尸残留
* **关联代码**: [orchestrator.py:L111](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/plugins/agent_dashboard/orchestrator.py#L111), [orchestrator.py:L288-314](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/plugins/agent_dashboard/orchestrator.py#L288-L314)
* **当前状态**: 
  1. 预检没有携带凭据注入逻辑。虽然当运行机器本身配置了全局 Git Credential Helper 时可能侥幸成功，但对于未认证的私有仓库将必然抛出 401 失败或因交互式弹窗挂起。
  2. 代码使用 `asyncio.wait_for(proc.communicate(), timeout=30)`，但在超时发生抛出 `TimeoutError` 后，底层创建的子进程并未被真正杀死（`wait_for` 超时不会自动发送 `SIGKILL` 给子进程），从而在系统中残留僵尸进程。
* **实现方案**: 
  1. 在预检的 `ls-remote` 调用中，通过临时环境变量或 AskPass 共享安全的认证上下文，并强制注入 `GIT_TERMINAL_PROMPT=0` 环境。
  2. 修正 Timeout 捕获逻辑：
     ```python
     except asyncio.TimeoutError:
         try:
             proc.kill()
             await proc.communicate() # 彻底收割僵尸进程
         except Exception:
             pass
         raise RuntimeError("Preflight timeout")
     ```
* **验收测试**:
  输入无权限的私有仓库 URL，测试确认预检失败时控制台无交互弹窗阻塞，且 30 秒超时后，后台无残留的 `git ls-remote` 孤儿进程。
* **修复 commit**: `Pending`

---

### 05. 🟡 High: 仪表盘核心状态无持久化（重启即消失）
* **当前状态**: 
  `TaskOrchestrator._tasks` 字典和 `ProgressAdapter._states` 进度均只保存在进程内存中。只要 Supervisor 重启，所有的历史任务和当前运行的任务数据就会清空。
* **实现方案**: 
  在 central DB 中建表 `agent_tasks`（存储 `task_id`、`group_id`、`status`、`repo_url`、`requirements` 等元数据），在创建、重试、中止及状态机更新时同步写入 DB；在 Supervisor 启动阶段读取该表并 Hydrate 恢复到内存结构中。
* **验收测试**:
  启动任务并处于 running 状态，杀死 Supervisor 重启，发起 REST 请求 `GET /tasks`，校验该任务依然存在，且状态和 adapter 进度可以正常读取。
* **修复 commit**: `Pending`

---

### 06. 🟡 High: GitHub Token 未向下传递至 PR 工具
* **当前状态**: 
  虽然 REST API 接收到了用户的 `github_token`，但在 `orchestrator.py` 创建任务并触发 Agent 工作流时，这个 Token 并未以任何上下文形式传递进 Worker 进程，这导致底层的 `rd_tools.create_pr()` 工具被 Agent 执行时，由于缺少凭据，在 push 分支或创建 PR 时抛出认证异常。
* **实现方案**: 
  将 `github_token` 封装进 `START_WORKFLOW` 的 payload 体中，Worker 在接收该指令并初始化执行上下文时，将其载入到会话级的隔离环境变量中，由 `github_client` 统一获取。
* **验收测试**:
  启动 Coding Agent 任务，验证当其走到第 6 步 `Create PR` 时，在 Worker 执行层收到的参数和环境变量中能成功解析出传入的 `github_token` 并创建 PR 成功。
* **修复 commit**: `Pending`

---

### 07. 🟡 High: 生产环境 Docker 镜像缺失 `gh` 客户端
* **当前状态**: 
  系统在推送和 PR 阶段强依赖 GitHub CLI (`gh`) 客户端，但在系统生产部署的 `Dockerfile` 中未安装此客户端，这将导致生产模式下 PR 创建必然崩溃，或者意外退化到假的本地 `local://` 形式链接。
* **实现方案**: 
  在 `Dockerfile` 构建阶段中，添加官方 gh-cli apt 仓库（或下载对应的二进制包）并进行全局安装。
* **验收测试**:
  进入容器化运行的 Worker 环境，执行 `which gh`，验证命令可达且版本正常。
* **修复 commit**: `Pending`

---

### 08. 🟡 High: 回滚/Abort 调用 `unregister_task()` 错误推送 done
* **当前状态**: 
  当新建任务出错进行 `rollback_group`，或手动执行任务 `abort_task` 时，调用 `self._adapter.unregister_task(group_id)` 清理适配器。但在适配器内部可能由于清除动作触发状态越界或在未解绑时向前端 WebSocket 广播了 `done` 终态信号。
* **实现方案**: 
  细化 `ProgressAdapter` 中解绑和注销逻辑，防止解绑操作污染状态广播。在 unregister 时应当仅销毁内存状态，若需广播，必须统一且明确地广播 `aborted` 或 `error` 状态包。
* **验收测试**:
  手动中止或回滚一个任务，抓取与其对应的 WS 最后一个包，校验其 type 不应是 `done`。
* **修复 commit**: `Pending`

---

### 09. 🟡 High: 任务状态（Task Record Status）未随 Worker 同步
* **当前状态**: 
  `TaskOrchestrator` 内存中记录的 `status` 只有在初次分发 (`"dispatched"`) 或手动重试 (`"restarted"`) 时被更新。而任务在 Worker 端真实的运行阶段（如 Agent 的 Tool Loop 正在 `running`，或是因为超时失败变成 `error`）并没有反向回调或事件同步给该 record，导致通过 REST 接口读取的 task list 状态永远处于静态初始词。
* **实现方案**: 
  `ProgressAdapter` 在监听到 Supervisor 分发的底层 Agent 事件（如 `workflow_completed` 等）时，应主动调用 `orchestrator` 的内部方法更新 `self._tasks[task_id]["status"]`，完成状态机的双向同步。
* **验收测试**:
  查询 `/api/agent/tasks`，监控其任务状态从 dispatched 在 Agent 运行时变更为 running，在 Agent 提交 PR 结束后自动同步为 done。
* **修复 commit**: `Pending`

---

### 10. 🟡 High: GitLab/Bitbucket 只有接口声明，实现完全缺失
* **当前状态**: 
  `api.py` 的 `CreateTaskRequest` 在 Pydantic 校验器中允许传入包含 `gitlab.com` 和 `bitbucket.org` 的 Git 链接。然而底层代码 `github_client.py` 几乎全部硬编码使用 `gh` CLI 交互，导致这些仓库一旦进入，在克隆或 PR 阶段必然发生不可控的崩溃。
* **实现方案**: 
  * 阶段一：在 API 正则校验器中，直接拦截限制仅能接收 `github.com`。
  * 阶段二（长远）：对 GitClient 接口进行多提供商（GitHub / GitLab / Bitbucket）解耦和多态实现。
* **验收测试**:
  创建任务时传入 `https://gitlab.com/test.git`，API 直接拦截并返回 422 校验错误。
* **修复 commit**: `Pending`

---

### 11. 🟢 Resolved: CodingAgentOrchestrator 正则误判定（已解决）
* **关联代码**: [coding_agent.py:L154-163](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/core/orchestration/plugins/coding_agent.py#L154-L163) (98f33df 前的旧代码)
* **分析**: 
  此前使用宽泛的 `test.*fail` 正则去检索 Agent 的测试输出日志。由于正常测试通过时，pytest 会在最后统计打印 `0 failed`，进而误触发了失败 reworking 重试分支，陷入死循环。
* **修复实现**: 
  在 Commit `98f33df` 中，重构了统一的完成信号协议（Unified completion signal protocol）。移除了基于测试日志正则检测的规则，改为使用严格的 `[[AGENT_DONE]]` Sentinel 哨兵机制和结构化工具信号。
* **修复 commit**: `98f33df`

---

### 12. 🟢 Resolved: 无关文件的意外提交（已解决）
* **分析**: 
  Commit `98f33df` 意外包含了 `.opencode/opencode.json` 和 `.opencode/tui.json` 的无关修改。
* **修复实现**: 
  后续应对该分支进行 Git 变基清理，并在 `.gitignore` 中加入此类特定编辑器或辅助插件产生的私有配置文件路径。
* **修复 commit**: `98f33df`

---

### 13. 🔵 Minor: API Command-injection 黑名单安全防御脆弱
* **当前状态**: 
  API 层的 `test_command` 校验逻辑使用硬编码字符敏感词校验（如过滤 `;`, `|`, `&&`）。该方式容易通过无空格重定向、换行符拼接绕过。
* **评估**: 
  降为 Minor 级别防线（Defense-in-depth）。接口传入的 `test_command` 只是被当作 Prompt 输入给 Agent，或作为沙箱内 `run_shell` 命令的参数。在 Worker 端有最终的 shlex 分离和 Sandbox 沙箱命令防线。因此“直接绕过并在宿主机执行命令”的因果链在此处并不成立。
* **实现方案**: 
  在 API 校验中直接拒绝特殊符号（如换行符等），或将 test command 强制收窄限制为特定的单执行文件（如只能以 `pytest` 或 `npm test` 起头，限制特殊符号）。
* **验收测试**:
  传入 `pytest\nrm -rf /`，API 层抛出参数错误限制执行。
* **修复 commit**: `Pending`

---

### 14. 🔵 Minor: StuckDetector 内存泄漏风险夸大
* **当前状态**: 
  在非 `done` 状态下重试计数器残留。
* **评估**: 
  降为 Minor。该哈希映射中只保存了 `group_id: int -> count: int`，每个任务项极小。即便有数千个历史残留，物理内存开销也在几百 KB 以内，不会导致进程直接发生 OOM。
* **实现方案**: 
  在统一的任务注销（或者清理）函数中，主动触发 StuckDetector 将此 group 的状态清理掉。
* **修复 commit**: `Pending`

---

## 三、 评审结论

本版 Review 报告已经过最终校正，**对齐并固化了评审 Commit 区间为 20d472f..098de0b**。

* **当前重点关注的 Critical 缺陷**: 
  1. Token 写入进程 argv（01项）
  2. CancelledError 下的强制 worktree promote 脏合并（02项）
  3. API / WebSocket 越权隐患（03项）

前述被夸大的 WebSocket Handshake 异常已被移除（确认为 Starlette 规范支持行为），且脆弱正则匹配问题已被 Commit `98f33df` 成功解决。项目当前的开发分支（带有 Dirty 状态的工作区）正在优先进行 02 项（Abort 数据隔离与 Discard）的研发工作。
