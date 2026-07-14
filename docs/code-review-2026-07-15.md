# 架构师审查报告 (2026-07-15)

## 编排与近期 Commit 深度多维度评估

本报告由架构师对 2026-07-15 近期提交的 8 个 Commit 进行多维度深度审查后整理归档。

---

## 核心发现一览 (Critical Findings)

| 编号 | 严重级别 | 维度 | 缺陷描述 | 潜在后果 |
| :--- | :--- | :--- | :--- | :--- |
| **01** | 🔴 Critical | 安全与并发 | `git config` 写入明文 Token，存在多任务覆盖竞态与物理泄漏风险 | GITHUB_TOKEN 泄漏；并发推送认证交叉污染 |
| **02** | 🔴 Critical | 鲁棒性与功能 | 私有仓库预检（Pre-flight）未注入 Token 且未禁用终端交互 | 私有仓库任务创建 100% 失败或 hang 挂起 |
| **03** | 🟡 Major | 资源与生命周期| `StuckDetector` 计数器在非 `done` 终态下不清理，引发内存泄漏 | 守护进程内存持续增长，长周期运行 OOM |
| **04** | 🟡 Major | 业务边界/可靠性| `CodingAgentOrchestrator` 正则过于脆弱，成功测试日志会被误判为失败 | 任务陷入无限重做（Rework）死循环 |
| **05** | 🟡 Major | ASGI 规范合规 | WebSocket 握手未 Accept 即调用 Close，违反 Starlette 状态机规范 | ASGI 服务器抛出 AssertionError 或客户端静默断开 |
| **06** | 🟡 Major | 安全与输入校验 | REST API 过滤规则极易通过换行符、无空格重定向绕过 | 恶意测试命令注入，执行任意 Shell 脚本 |
| **07** | 🔵 Minor | 健壮性 | 简单字符串替换处理 URL，当输入包含 Username 时生成畸形 URL | 克隆私有仓库失败 |

---

## 深度技术剖析 (Detailed Analysis)

### 01. 🔴 安全与并发：`git config credential.helper` 导致明文 Token 泄漏及竞态
* **关联代码**: [github_client.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/integrations/github_client.py#L134-L155)
* **分析**:
  代码在推送分支时，为了支持私有仓库认证，动态修改了 Git 配置：
  ```python
  if github_token:
      await _git("config", "credential.helper",
                 f"!f() {{ echo 'password={github_token}'; }}; f", cwd=cwd)
  ```
  这存在两个严重的系统级问题：
  1. **多 worktree 竞态风险**: 
     在 Git 中，默认情况下多个 worktree **共享同一个** `.git/config`（除非显式开启 `extensions.worktreeConfig` 并使用 `git config --worktree`）。若不同 Group 的 Worker 进程在同一父仓库的 worktree 中并发执行 push，它们会激烈地抢占、覆写 `.git/config` 中的 `credential.helper`。这会导致 A 任务使用 B 任务的 Token 进行推送，产生严重的跨租户鉴权污染。
  2. **物理文件残留与 Token 泄漏**:
     如果 Worker 进程在 `finally` 块执行前遭遇不可抗力崩溃（如 OOM 被系统 OOM-killer 强杀、主机掉电、容器强收缩等），该凭证助手将**永久残留在磁盘 `.git/config` 中**。任何拥有容器/宿主机只读权限的实体都可以直接窃取该明文 `GITHUB_TOKEN`。
* **架构改进建议**:
  绝不能通过持久化修改磁盘配置文件来传递单次会话的凭证。应当使用 Git 命令行参数 `-c` 动态覆盖配置，生命周期仅局限于该次 push 进程：
  ```python
  # 改进后：配置只留在进程内存中，不落地磁盘，不存在竞态
  await _git("-c", f"credential.helper=!f() {{ echo 'password={github_token}'; }}; f", "push", "-u", remote, branch_name, cwd=cwd)
  ```

---

### 02. 🔴 鲁棒性：私有仓库 Pre-flight 预检必败且可能挂起
* **关联代码**: [orchestrator.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/plugins/agent_dashboard/orchestrator.py#L111) 与 [orchestrator.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/plugins/agent_dashboard/orchestrator.py#L288-L314)
* **分析**:
  在任务创建起点，系统提供了一个极好的“Pre-flight check”机制防止资源浪费：
  ```python
  await self._preflight_check_repo(repo_url, base_branch)
  ```
  但该方法根本没有接收 `github_token` 参数！
  当用户提供一个私有仓库 URL 配合 `github_token` 时：
  1. 预检执行 `git ls-remote --exit-code https://github.com/...`，由于没有携带 Token，GitHub 会返回 `401 Unauthorized`，导致预检被 Fail-Closed 熔断，任务直接回滚。
  2. 更加危险的是，该预检子进程没有在环境变量中声明 `GIT_TERMINAL_PROMPT=0`。在某些网络或凭证状态下，`git` 会弹框提示输入密码，由于这是一个后台无交互进程，子进程将在此处**静默挂起 30 秒**直到被 `wait_for` 超时终止，严重拖累 Supervisor 的吞吐量。
* **架构改进建议**:
  预检必须能够处理私有凭证，并确保绝不进入交互式挂起：
  ```python
  async def _preflight_check_repo(self, repo_url: str, branch: str = "", github_token: str = "") -> None:
      # 1. 注入 Token
      if github_token and repo_url.startswith("https://"):
          repo_url = repo_url.replace("https://", f"https://{github_token}@", 1)
      
      args = ["git", "ls-remote", "--exit-code", repo_url]
      if branch:
          args.extend(["--heads", branch])
          
      # 2. 强置非交互模式
      env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
      proc = await asyncio.create_subprocess_exec(*args, env=env, ...)
  ```

---

### 03. 🟡 资源与生命周期：`StuckDetector` 内存泄漏
* **关联代码**: [stuck_detector.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/plugins/agent_dashboard/stuck_detector.py#L70-L83)
* **分析**:
  在卡死检测器的定期循环中，通过 `self._retry_counts` 对每个 Group 进行重试记数：
  ```python
  # 任务完成时，清理计数器
  if state.status == "done":
      self._retry_counts.pop(group_id, None)
      continue

  # 遇到其他终态（如出错、被强行中止、彻底卡死），直接跳过
  if state.status in ("error", "aborted", "stuck", "stuck_permanently"):
      continue
  ```
  注意看！当任务状态变成 `"error"`（运行出错退出）或 `"aborted"`（用户手动取消）时，代码直接 `continue` 了！
  这意味着，一旦任务没有以 `"done"` 成功收尾，这个 `group_id` 对应的重试次数将**永久滞留**在 `self._retry_counts` 字典中。对于一个高吞吐量、长时间运行的机器人协作后台，随着失败/取消任务的累积，这里将发生缓慢但确定无疑的内存泄漏。
* **架构改进建议**:
  在任何任务达到**终态**（无论成功或失败）时，彻底注销其在检测器中的临时状态：
  ```python
  # 统一处理所有终态的内存释放
  if state.status in ("done", "error", "aborted", "stuck_permanently"):
      self._retry_counts.pop(group_id, None)
      continue
  ```

---

### 04. 🟡 可靠性：脆弱的正则匹配导致成功测试被误判为失败
* **关联代码**: [coding_agent.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/core/orchestration/plugins/coding_agent.py#L154-L163)
* **分析**:
  在单 Bot 协同的 `observe` 阶段，系统自动根据日志正则识别任务是否失败：
  ```python
  error_patterns = [
      r"测试失败", r"test.*fail", r"error.*cannot.*fix",
      ...
  ]
  ```
  其中 `test.*fail` 是一个灾难性的正则：
  在绝大多数测试框架（如 `pytest`）正常通过时，往往会打印类似以下总结：
  `tests/test_auth.py: 12 passed, 0 failed in 1.2s`
  或者
  `test_api.py ......... [100%] (0 failed)`
  这一行内容包含了 `test` 且后面包含了 `fail` (在 `failed` 单词中)。这会导致 `re.search(r"test.*fail", ...)` 成功匹配！
  这意味着，即使代码实现完美，测试 100% 通过，Orchestrator 也会粗暴地判定“任务失败”，从而拦截本该正常提交的 PR，逼迫 Agent 陷入无意义的 `rework` 死循环，直到用光最大重试次数抛出异常。
* **架构改进建议**:
  测试日志的成功/失败提取不能使用如此宽泛且不加锚定的正则。应该通过限定边界，如匹配有正整数的 failure 统计，或者移除该宽泛的正则，仅依赖特定的终结符和明确的失败标识（如 `pytest` 非零退出码，或具体框架的错误关键字）。
  ```python
  # 改进匹配精度，防止误判
  error_patterns = [
      r"测试失败", r"failed:\s*[1-9]\d*", r"ERROR:", r"无法修复"
  ]
  ```
  此外，Sentinel 检测 `AGENT_DONE` 时，代码里使用 `.replace("[", "").replace("]", "")` 剥离了括号。这导致如果 Agent 在普通文本中提到 "AGENT_DONE" 也会触发成功结束。建议**严格保留括号进行匹配**，将其作为确定性的控制信号。

---

### 05. 🟡 协议规范：WebSocket 握手异常处理不符合 ASGI / Starlette 规范
* **关联代码**: [websocket.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/plugins/agent_dashboard/websocket.py#L84-L93)
* **分析**:
  在 `websocket.py` 的授权拦截中：
  ```python
  if not token:
      await ws.close(code=4001, reason="Missing authentication token")
      return False
  ```
  在执行 `ws.close()` 时，底层的 WebSocket 连接**尚未被 Accept**（`dashboard_ws` 函数中 `await ws.accept()` 在这之后执行）。
  在 ASGI 协议以及 FastAPI (Starlette) 内部状态机中，对一个处于 `connect` 阶段（未接受）的 Socket 直接发送 `close` 事件可能会导致部分 ASGI 服务器（如 Uvicorn / Hypercorn）抛出未捕获 of 运行时异常（如 `AssertionError: Cannot call close before accept` 或类似状态异常），并导致连接无法优雅断开。客户端也不会收到预期的 `4001` 关闭状态码，只会观察到通用的 TCP 层面异常关闭。
* **架构改进建议**:
  遵循 `main.py` 中已经沉淀的成熟 WS 鉴权拒连模式：先 Accept 握手，写入带有明确错误代码的 JSON 信息，然后再关闭：
  ```python
  # 改进后：保证 ASGI 状态机完整，让前端能明确收到 auth_error 事件
  await ws.accept()
  await ws.send_json({"type": "auth_error", "message": "Authentication required"})
  await ws.close(code=4001)
  ```

---

### 06. 🟡 安全与输入校验：REST API 卡死重试命令的过滤防护存在绕过
* **关联代码**: [api.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/plugins/agent_dashboard/api.py#L74-L82)
* **分析**:
  在 REST API 层针对 `test_command` 做了防注入校验：
  ```python
  dangerous = ["|", ";", "&&", "||", "`", "$(", "> ", "< ", "curl", "wget", "eval", "bash"]
  ```
  这种黑名单机制极其脆弱，很容易被刻意构造绕过：
  1. **无空格重定向**: 校验了 `"> "`（带空格），但没有校验 `>`。攻击者只需传入 `pytest >/tmp/evil.sh` 即可完成文件写入。
  2. **换行符命令拼接**: 校验没有包含 `\n`。攻击者如果传入：
     ```bash
     pytest
     rm -rf /
     ```
     在 Shell 顺序执行时，这会分行执行，彻底绕过所有分号或 `&&` 的过滤。
  3. **非 bash 的 shell 启动**: 仅封禁了 `bash`，但 `sh`、`zsh`、`python -c` 均可长驱直入。
* **架构改进建议**:
  尽管在 Worker 执行侧还有最终的 Shell 防护，但 API 边界校验应遵循“fail-fast”原则。建议：
  * 使用字符白名单，限制 `test_command` 仅能包含常见命令、字母、数字、点及有限的安全参数。
  * 或者，使用 `shlex.split` 进行结构解析，确保不能解析出管道、重定向或多条独立命令。
  ```python
  # 强置字符集限制，禁止换行和重定向相关符号
  if any(char in v for char in ("\n", "\r", ";", "&", "|", "`", "$", "<", ">")):
      raise ValueError("Disallowed shell characters detected")
  ```

---

### 07. 🔵 健壮性：私有 URL 凭证注入方式不够优雅，易损坏原 URL
* **关联代码**: [github_client.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/integrations/github_client.py#L103-L105)
* **分析**:
  代码在拼接 HTTPS 克隆地址时，使用了简单的字符串替换：
  ```python
  if github_token and repo_url.startswith("https://"):
      clone_url = repo_url.replace("https://", f"https://{github_token}@", 1)
  ```
  如果用户传入的 `repo_url` 本身就已经携带了用户名，例如：`https://oauth2@github.com/org/repo.git`，
  经过该逻辑替换后，会得到：`https://<token>@oauth2@github.com/org/repo.git`。
  含有双 `@` 符号的 URL 会直接导致 Git 底层解析失败，抛出畸形地址错误。
* **架构改进建议**:
  应当使用 Python 标准库中的 `urllib.parse` 进行规范的 URL 重组，而不是粗暴地操作字符串：
  ```python
  from urllib.parse import urlparse, urlunparse
  
  parsed = urlparse(repo_url)
  if github_token and parsed.scheme == "https":
      # 替换 netloc 中的 user:pass 部分
      netloc = f"{github_token}@{parsed.hostname}"
      if parsed.port:
          netloc += f":{parsed.port}"
      clone_url = urlunparse(parsed._replace(netloc=netloc))
  ```
