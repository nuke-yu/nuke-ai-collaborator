# Jira Story 与 Epic 驱动的在线编码任务

设计规格草案 · 2026-11-24 · Nuke AI Collaborator

**本次升级只针对 Collaborator 的第三个方向「在线多任务执行平台」。** 现有在线编码 API 可以发起单仓任务。本次在它上面增加 Epic 入口，把一批同类的服务改造组织起来，统一派发、恢复、验收和查看交付进度。多 Agent 协作和多轮对话是独立演进的两个方向，本规格只复用它们已有的 Group 隔离、会话入口和人工审批能力。

操作者可以直接输入一个 Jira Story，也可以输入 Epic，让 Collaborator 查全其下的 Story。两种入口都根据每个未完成 Story 的描述和验收条件，在对应的 GitHub 服务仓库中修改代码、运行测试并提交 PR。操作者从 Epic 总览看到哪些服务已合入、哪些 PR 等待审查、哪些任务需要补充信息，并能直接进入相应 Story 的执行会话。

**一个 Story 对应一个服务仓库和一个逻辑编码任务。** 重试产生新的执行尝试。Epic 组织这些任务，Group 隔离一次服务任务的成员、会话、工作区和记忆。同一 Jira 站点和 issue ID 只登记一个 Story 任务，单项提交和 Epic 可同时引用它。不同 Story 可以映射到同一个仓库，各自使用独立的 Group、worktree 和分支。

## 1. 使用场景和交付口径

以一个“升级依赖 X”的 Epic 为例。某个 Story 要求订单服务升级到指定版本并通过订单创建回归用例。系统从 Story 的服务标识定位订单服务仓库，把目标版本、该 Story 的验收条件和仓库验证命令交给 Coding Agent。Agent 检查当前版本、修改依赖和必要的兼容代码，在任务 worktree 中完成验证。操作者可从总览打开测试证据和 PR，或在订单服务的会话中要求补充一个用例。另一个库存服务 Story 可以独立失败、补充信息或重试。

| 事实 | 决定方 | 如何呈现 |
|---|---|---|
| 做什么、属于哪个 Epic | Jira 中的 Epic、Story 和验收条件 | 保存本次采用的需求快照及来源版本 |
| 本次改哪个仓库、哪个分支 | 经授权的服务映射和任务配置 | 在 clone 前展示并固定仓库和目标分支 |
| 代码是否满足验收条件 | 对应提交的检查结果和审查人 | 展示测试命令、退出码、证据及 PR |
| PR 是否已合入 | GitHub 上目标 PR 的实际状态 | 人工合入后同步为已合入，关闭未合入另计 |

首版面向版本升级、FOSS issue 修复等同类任务。升级要求记录前后版本及验证结果，缺陷修复应记录可复现的失败与修复后的回归证据。Jira 在首版按只读需求源接入；Collaborator 执行结果与 Jira 工作流状态分别显示。

## 2. 接入现有架构

### 三个方向的代码边界

| 能力 | 入口与代码归属 |
|---|---|
| 多 Agent 协作 | 群工作流入口交给已注册的编排器。角色分工、阶段推进、人工确认由现有工作流管理。[群工作流 API](../../../backend/api/workflow.py) · [声明式编排器](../../../backend/core/orchestration/declarative.py) |
| 多轮对话 | 用户消息进入所属 Group 的 Worker，由现有消息派发处理回复、上下文和连续发言。协作和对话可以在同一 Group 中自然切换。[消息派发](../../../backend/runtime/dispatch.py) · [对话路由](../../../backend/core/orchestration/declarative.py) |
| 在线多任务执行 | Dashboard 提供 HTTP/WS 入口。`online_tasks` 拥有单仓任务创建、重试、状态存储、恢复和 Worker 端编码工作流。Epic 队列、批次预算和 PR 验收也归此包。Worker 通过现有编排器扩展点发现编码工作流。[任务服务](../../../backend/online_tasks/service.py) · [任务存储](../../../backend/online_tasks/store.py) · [编码工作流](../../../backend/online_tasks/workflow.py) |

依赖方向从在线任务指向共享的 Group、工作区、工具和执行契约。Jira Story 标识、批次重试及 PR 状态属于在线任务域，普通聊天与协作的输入仍使用既有协议。跨进程继续传递结构化 IPC。原有独立单仓请求以及持久化的 `coding_agent_v1` 工作流沿用现有标识。

### Epic 功能的进程分工

| 所有者 | 现有实现和新增职责 |
|---|---|
| Supervisor 侧的 Dashboard 插件 | 单仓入口当前先建 Group/Bot、clone，再插入任务记录并发 IPC。新增 Epic API、发现进度、Story 队列、调度和汇总。扩展现有任务创建服务，让已预留身份的 Story 能分阶段准备、恢复和派发。[任务创建实现](../../../backend/online_tasks/service.py) |
| 拥有 Group 的 Worker | 继续执行工作流、创建和绑定 worktree、调用现有 Python 执行器。新增批次专用的验收和交付策略。现有 Dashboard 成功分支会执行本地 promote，批次把远程 PR 状态作为交付依据。[Runner](../../../backend/core/runner.py) · [编码工作流](../../../backend/online_tasks/workflow.py) |
| Jira 读取适配器及 MCP Collector | 当前默认 Jira 客户端操作 Group 内的本地工单表。新增按连接和 Epic 枚举真实 Story 的只读契约。若通过 MCP 读取，Supervisor 派发有身份的读取请求给有权的 Worker，Worker 执行权限判断，经 IPC 调用唯一持有连接的 Collector，再返回结构化分页结果。[本地 Jira 接口](../../../backend/integrations/jira.py) · [Worker MCP 代理](../../../backend/executors/providers/mcp_proxy.py) |
| 中央 SQLite 与各 Group DB | 中央库保存 Epic、Story 关联、队列和可汇总结果。每个 Group 保存会话全文、工具明细、测试日志及 memory。Worker 在本组事务提交后通过 IPC 发送可重放的结果摘要；Supervisor 凭事件 ID 去重并更新中央视图。[Group DB 路径](../../../backend/runtime/dbpaths.py) · [MemoryScope](../../../backend/memory/domain/scope.py) |

Dashboard 插件挂载 Epic API 并在 Supervisor 进程内装配 Jira 发现、队列、调度和进度汇总服务。批次业务规则由 `online_tasks` 实现。Worker 根据任务是否关联 Epic 来选用批次验收和交付策略，共用的单仓任务入口继续支持独立调用。首版使用现有 Python 执行循环，DSH 和 Superagent 作为设计参考。[现有执行循环](../../../backend/executors/plugins/tool_loop_v1.py)

### 2.1 DSH 为单个 Story 的执行提供什么参考

| DSH 源码中的行为 | 在 Collaborator 中如何采用 |
|---|---|
| Headless 恢复时校验会话身份和工作目录。DSH 另行定义了执行中的消息投递时机。[会话恢复](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/bundle/headless/src/index.ts#L383) · [消息投递](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/core/agent-loop/src/agent.ts#L177) | 本次借鉴会话与工作目录的绑定。每次尝试记录 Group、Session 和 worktree，恢复时核对归属。总览链接到已有 Group 会话，操作者在其中补充要求时沿用现有消息投递方式。 |
| Headless 执行结束时先写回会话，再依据本轮结束原因设置进程退出码。[结束处理](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/bundle/headless/src/index.ts#L433) | 执行器返回会话结束原因，验证步骤另行记录实际测试命令、退出码和对应提交。同一轮 Agent 即使正常结束，缺少测试证据的 Story 仍停在验证环节，按第 5 节处理。 |
| 工具集可在单个 Agent 的上下文中收窄，执行管线会处理实际工具调用的审批决定。[工具范围](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/core/tools/src/index.ts#L162) · [执行审批](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/core/tools/src/pipeline.ts#L191) | 把第 6 节的仓库、分支和可写动作绑定到 Story。沿用 Worker 的权限检查和人工审批（HIL），MCP 调用经已有代理交给 Collector。拥有 Epic 操作权的用户仍须具备对应 Group 的访问权才能查看会话全文。 |

### 2.2 Superagent 为一个 Epic 下的多项任务提供什么参考

| Superagent 源码中的行为 | 在 Collaborator 中如何采用 |
|---|---|
| 持久队列按动作类型和键写入，结束时校验队列记录的序号和版本，防止旧执行删除更新后的动作。[动作写入](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/8c32a9dc5d72c033816c6a8110b37f40cf94fba4/src/app/effects.ts#L62) · [完成校验](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/8c32a9dc5d72c033816c6a8110b37f40cf94fba4/src/app/effects.ts#L133) | Supervisor 在同一事务内保存 Story 状态和待派发动作。Worker 回报须带任务 ID 和尝试序号。发布 PR 后若丢失回执，按第 4 节核对远端，再决定是否重发。 |
| `WorkerSlots` 在异步启动前预留执行名额。容量已满时通过 `Later` 延后，队列将它与执行失败分别处理。[预留名额](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/8c32a9dc5d72c033816c6a8110b37f40cf94fba4/src/executor/dsh.ts#L192) · [延期处理](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/8c32a9dc5d72c033816c6a8110b37f40cf94fba4/src/app/effects.ts#L138) | 把名额控制放到能看到所有 Worker 的 Supervisor。一个 Story 在 clone 前领取名额，等待容量时保持排队，进入待审后释放。80 个服务可以一次登记，实际启动数同时受 Epic 上限和系统批量上限约束。 |
| Ticket 表示持续存在的任务，Assignment 记录它与一次执行器实例的绑定、起止时间和报告。[Ticket](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/8c32a9dc5d72c033816c6a8110b37f40cf94fba4/src/core/model.ts#L281) · [Assignment](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/8c32a9dc5d72c033816c6a8110b37f40cf94fba4/src/core/model.ts#L218) | 一个 Jira Story 对应一个稳定任务。重试创建新的尝试记录，保留前次失败的提交和测试证据。Epic 按 Story 计交付数，按尝试累计资源消耗，复用现有单仓任务和重试入口。 |
| 对仍在跟踪 PR 的 Ticket，`ready` 可显示为等待人审，后续能区分 PR 已合入和关闭未合入。[审查阶段](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/8c32a9dc5d72c033816c6a8110b37f40cf94fba4/src/core/stage.ts#L15) · [关闭结果](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/8c32a9dc5d72c033816c6a8110b37f40cf94fba4/src/core/tickets.ts#L77) | 测试证据和 PR 均已核实后，Story 进入 `needs_review`。人负责合并，Supervisor 依据 GitHub 的实际状态汇总 `merged` 或 `closed_unmerged`。 |

## 3. 发现并安排 Story

1. 有操作权限的用户提交 Story 或 Epic key 和自己提供的仓库映射。系统先落盘请求，再返回执行 ID。首版通过部署配置连接一个 Jira Cloud 站点。
2. 读取 Epic，校验类型和项目访问权。分页读取该 Epic 的直接子 Story，逐页持久化发现结果和游标，按 Jira 连接及稳定的 issue ID 去重。发现阶段完成后，才开放本批次派发。页中断显示“发现中，已读 N 项”；成功读完且零项时显示“无 Story”。如果可见范围受限或连接报告部分结果，标明范围或阻塞原因。
3. 所有未完成 Story 都符合派发条件，包括 Jira 中正在进行的 Story。已完成 Story 展示为跳过。每个 Story 使用操作者提供的 GitHub 仓库、目标分支和可选测试命令。没有映射、映射冲突或缺少可用描述时单项阻塞。重新提交同一请求可补齐缺失的映射。
4. 为每个 Story 固定本次输入快照，包括 Epic 共同目标、Story key、描述、验收条件、服务映射及模板版本。缺少明确目标时，在 Story 上请求补充。重复提交同一 Epic 返回已有执行。显式同步用于发现新增 Story，已执行的 Story 保留原始输入；内容变化标记为待确认，新版本通过有记录的重试采用。已移出 Epic 的排队项停止派发，已启动项提示操作者处理。
5. 每获得一个名额，调度器领取一个就绪 Story，复用任务服务建立其专用 Group、Bot 和 checkout，记录目标分支基线 SHA。Worker 在任务 worktree 中编码和测试。若配置了明确的前置 Story 关系，则等待前置项满足约定的交付条件；循环或无法解析的依赖标作阻塞。

## 4. 数据和调度契约

下表是拟新增的逻辑记录。现有 `agent_tasks` 以 `group_id`、`bot_id` 为必填项，无法直接表示尚未建组的 Story。先落盘 Story 执行意图，再关联现有任务，可保留单仓 API 的交互方式。[任务表](../../../backend/db/schema.py) · [现有任务存储与重试领取](../../../backend/online_tasks/store.py)

| 记录 | 标识和最小字段 |
|---|---|
| Epic 执行 | 执行 ID、授权操作者、Jira 连接和 Epic ID/key、发现游标和完成时间、调度开关、并发上限、预算、模板版本。同一授权域和连接下的 Epic 在首版对应一个可继续的执行记录 |
| Story 任务 | Epic 执行 ID、Jira issue ID/key、需求快照版本、服务、仓库、base ref/SHA、状态、阻塞原因、可空的既有 task ID、PR 标识。对同一执行中的 issue ID 建唯一约束 |
| 执行尝试 | Story 任务 ID、尝试序号、Worker、Session、分支、起止时间、阶段、测试证据引用、提交 SHA、失败类型和消耗。重试保留历史尝试 |
| 待派发动作 | 唯一动作键、关联任务和尝试、动作类型、最早执行时间、领取租约、次数和最后错误。业务状态和下一步动作在同一中央库事务内提交 |

**并发名额从准备仓库前开始占用，持续到编码、测试和发布动作结束。** 等待人工审查的 PR 释放名额，后续审查反馈需要修改时再领取。调度器同时遵守系统批量任务上限和 Epic 上限，为聊天与协作预留资源。首版默认每个 Epic 同时执行 1 个服务，配置可提高；80 是批次规模，实际并发由模型配额、测试资源和磁盘容量决定。暂停停止领取新任务，已开始的任务继续；显式取消单项时才通知所属 Worker 停止。停止 Epic 时保留已创建的 PR 和审计记录。

领取通过 SQLite 条件更新和有期限的租约完成。每次派发携带稳定任务 ID 和尝试序号，Worker 在开始和回报结果时检查归属。持久化意图后才发起 clone、IPC、push 等操作。进程可能在外部操作成功后、写入回执前退出，因此恢复时需要对账。

- **准备中断**。先按持久预留的任务标识找回已建 Group/Bot 和工作区，验证 remote、base 和 worktree 归属。仅完整 checkout 可以继续使用，部分 clone 可单独重建。准备中断仍在总览可见。
- **派发中断**。租约到期先向原 Worker 查询该尝试。确认未运行或已停止后才接管。重复 IPC 只返回同一次执行状态；旧尝试的迟到回报不能覆盖新尝试。
- **发布中断**。以仓库、base、稳定任务分支和 head SHA 查询远程分支与已有 PR，匹配时补记回执。同一 Story 重试复用匹配的 PR。远端出现预期外提交时交给人工处理。网络超时和限流采用有上限的退避；测试失败和需求不明分别保留证据，按原因修复或交给人。

按尝试汇总耗时、token、有定价依据的成本、失败类型和人工介入次数。预算或配置的批量失败停止线触发时暂停新派发，展示触发原因。每个 Story 只计一次，重试消耗累计。

## 5. 编码、验证与人工交付

Story 的主要顺序为 `queued → preparing → running → verifying → publishing → needs_review → merged`。每一步记录时间和尝试号。验证失败进入 `failed`，权限、需求、服务映射等需要人处理的问题进入 `blocked`。人工关闭 PR 进入 `closed_unmerged`，撤销任务进入 `cancelled`。仓库已满足目标且检查通过时允许 `no_change`，同时保留基线 SHA 和验证证据。

**进入 `needs_review` 的条件**是 Worker 从实际执行取得验证命令、退出码和对应的代码版本，并从 GitHub 确认匹配的 PR。验证记录包含 Story 验收项、修改文件、base/head SHA、检查结果和本组日志引用。失败、超时、未运行各自可见。若发生新的代码修改，前一版本的成功记录只作为历史。已发布 PR 的后续 CI 独立显示为等待、通过或失败。

目前编码工作流检查 `create_pr` 成功信号，GitHub 客户端可以在创建 PR 的过程中提交并推送，还会从远端默认分支推断 base。批次交付必须在发布前固定已验证的提交或确认生成提交的文件树与已验证版本相同，在调用 GitHub 时显式指定 Story 的目标仓库、base 和任务分支。为 `no_change` 提供有验证证据的结束路径。人工在 GitHub 合并；本地 worktree 的 promote、Bot 的结束信号和 PR 创建各自独立记录。[编码工作流](../../../backend/online_tasks/workflow.py) · [GitHub 发布逻辑](../../../backend/integrations/github_client.py)

总览按 Story 展示队列、运行中、待审、已合入、无需改动、阻塞、失败、关闭和取消。仅当发现已完成且全部 Story 为 `merged` 或 `no_change` 时显示“全部交付”。存在待审 PR 时显示等待审查；其余已结束但未交付的 Story 单列。Jira 写回留作可选集成，合入事实以 GitHub 为准。

## 6. 单仓任务的上下文和工具权限

**服务架构地图随仓库版本读取。** 最小地图列出服务入口、关键模块、对外接口、配置位置、测试入口和常用验证命令，并记录来源路径与 commit。优先使用仓库内已有的 `AGENTS.md`、构建清单和目录说明。地图缺失时按需扫描当前 checkout；版本不符时重新核对相关路径。Agent 在修改前读取实际实现，与源码冲突时记录差异。首次运行一个 Epic 只需准备其中用到的仓库。

**每个 Story 沿用所属 Group 的会话和记忆隔离。** Epic 派发给各 Group 的材料限于明确授权的共同目标、版本化模板和本 Story 输入。中央视图只汇总任务状态、耗时和允许展示的产物引用，查看原始证据须校验 Group 权限。单仓工作流自行管理 Bot 分工与会话上下文，调度器只根据任务状态领取工作、接收结果。

**权限按任务的仓库、分支和动作约束。** 当前 Dashboard 为专用 Bot 配置了 `bypassPermissions`。Epic 模式需要绑定任务范围的规则集和受控发布路径，覆盖内置 Git、shell 和 MCP 等实际可写通道。Worker 在转发 MCP 前完成权限判断，Collector 继续管理共享连接及并发限制。GitHub 写权限限于任务交付所需动作，目标分支受保护，合并保留给人。对必须人工批准的额外操作，任务停在 `blocked` 并记录理由。[Dashboard Bot 配置](../../../backend/online_tasks/service.py) · [MCP Collector](../../../backend/runtime/mcp_collector.py)

Jira 描述和工具结果作为数据处理。入库存储文本、IPC 摘要及导出遥测前，调用 `redact_secrets()` 并解包 `(text, count)`，对脱敏文本限长。过长需求保留来源与截断标记；关键验收内容缺失时补读或阻塞。网络 I/O 使用异步客户端，遥测导出置于事务提交之后且不阻塞 Worker。[脱敏函数](../../../backend/executors/redaction.py)

## 7. 操作接口草案

| 操作 | 接口和结果 |
|---|---|
| 输入 Epic | `POST /api/agent/epic-runs`。输入 `jira_connection_id`、`epic_key`，可选 `max_parallel`。首次落盘返回 202 和执行 ID；相同意图重放返回已有 ID。沿用单仓 API 的 operator 认证和幂等键机制 |
| 查看进度 | `GET /api/agent/epic-runs/{id}` 返回发现进度和分状态计数。其 `/stories` 子资源支持按状态分页，每项可跳转到任务会话、验证证据和 PR |
| 同步、暂停、继续、停止 | `POST /api/agent/epic-runs/{id}/{sync\|pause\|resume\|stop}`。服务端按当前状态处理重复请求，返回受影响的排队项和运行项数量 |
| 处理例外 | `POST /api/agent/epic-runs/{id}/stories/{story_id}/{retry\|cancel}`。单项重试领取已有 task 的新尝试。相同请求重放返回原尝试。补充映射或需求时保留修订来源和操作者 |

### 首版 API

服务端启动时读取 `NUKE_JIRA_URL`、`NUKE_JIRA_EMAIL` 和 `NUKE_JIRA_API_TOKEN`。可用 `NUKE_JIRA_STORY_TYPE`、`NUKE_JIRA_EPIC_TYPE` 指定工单类型名称，用 `NUKE_JIRA_ACCEPTANCE_FIELD` 指定独立的 Jira 验收条件字段。GitHub 继续使用现有的 GitHub CLI 和服务端 GitHub 集成配置。Jira Cloud 搜索接口使用分页游标。[Jira Cloud 搜索接口](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/)

**单个 Story** 使用 `POST /api/agent/story-runs`。

```json
{
  "story_key": "PAY-123",
  "repository": {
    "repo_url": "https://github.com/example/payment",
    "base_branch": "main",
    "test_command": "pytest -q"
  }
}
```

**整个 Epic** 使用 `POST /api/agent/epic-runs`。

```json
{
  "epic_key": "PAY-100",
  "max_parallel": 2,
  "mappings": {
    "PAY-123": {
      "repo_url": "https://github.com/example/payment",
      "base_branch": "main",
      "test_command": "pytest -q"
    },
    "PAY-124": {
      "repo_url": "https://github.com/example/billing",
      "base_branch": "main",
      "test_command": "npm test"
    }
  }
}
```

两个入口都返回 HTTP 202 和进度查询 URL。响应汇总已完成、排队、阻塞、待审、已合入和关闭等状态。Epic 默认为一个并发名额，全局 Jira 队列默认两个名额，可通过 `NUKE_JIRA_MAX_PARALLEL` 配置。Epic 提供 `sync`、`pause`、`resume` 和单 Story `retry`，直接提交的 Story 提供 `retry`。已经创建的 Coding Task 可以沿用控制面的终止入口。

待审前，Worker 在同一 worktree 上重跑映射配置的测试命令并记录对应的提交。省略命令时，Worker 采用本轮在仓库根目录执行成功的受支持测试命令。Supervisor 跟踪仓库、目标分支和任务分支匹配的 PR。遇到发送结果不明且 Worker 没有持久化工作流的情况，任务显示 `interrupted`，供操作者先通过已有任务入口终止，再发起重试。

## 8. 实施与验收

先接通 Epic 分页读取和只读总览，确认 Story、服务、目标分支映射。再贯通一个服务的编码、可验证的交付和人工审查。随后接入持久派发、租约恢复和单项重试，按典型服务、特殊服务和核心服务做小批试点，再扩大到约 80 项。部署时需明确 Jira 连接、Story 类型与服务字段、授权服务目录及系统并发预算。

| 验收情形 | 可观察结果 |
|---|---|
| Epic 有 80 个 Story，读取第二页时断网 | 总览显示发现未完成。恢复分页后恰有 80 项，重复提交 Epic 或重放页面不增加任务 |
| 两个 Story 映射到不同服务，其中一项映射缺失 | 正确项正常执行，缺失项单独阻塞。补充后仍使用原 Story 任务 |
| clone 中断、Worker 重启，或两个调度请求同时重试 | 状态可追踪，同一尝试只有一个有效执行者。新的尝试不重复创建 Group、分支或已存在的 PR |
| 第一次尝试的完成回报迟于第二次尝试到达 | 回报只归档到第一次尝试，当前 Story 仍显示第二次的状态，其待派发动作仍可领取 |
| 批次名额已满，一个运行中的 Story 完成验证和发布，进入待审 | 其余 Story 在名额释放前保持排队，等待容量期间的失败计数和重试消耗均为零。名额释放后下一个就绪 Story 可以启动 |
| 测试失败、未运行，或修改后沿用旧提交的测试报告 | 不进入待审，展示实际原因和证据。目标已满足且验证通过的服务以 `no_change` 结束 |
| 创建 PR 成功后响应丢失，或 PR 被人工关闭 | 通过远端对账找回已有 PR。人工合入和关闭未合入分别计数；非默认目标分支得到正确 PR |
| 兼容性回归：Epic 满载、暂停或重启恢复期间，在普通 Group 继续已有对话和多 Bot 工作流，并通过原 API 提交独立单仓任务 | 批次遵守并发上限和暂停状态。普通 Group 的对话可接续、工作流按原角色分工执行，独立任务可按原流程交付。按部署约定的交互延迟预算验证资源隔离 |
| 一个 Story 尝试读取另一组历史或写入另一仓库 | 拒绝越权访问。注入测试用 Bearer token 后，中央摘要、追踪和归档日志均无明文 |
| 架构地图过期或缺失 | Agent 从当前基线重新定位代码与测试，并在结果里记录实际路径和版本 |
