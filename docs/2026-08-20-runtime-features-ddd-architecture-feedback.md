# Runtime Features DDD / 可复用架构 Review Feedback

日期：2026-08-20  
审查范围：最近实现的 4 组功能及其直接依赖

- `9232974`：Code Mode `tools.bash`
- `a377b52`：插件 IoC dependency injection
- `3b86841`：插件 EventBus disposer
- `176a74a`：StorageAdapter contract

同时参考了前置实现：Read-Before-Mutate、Spill Policy、Tool Executor middleware、`nuke.patch.yml`。

## 一、总体结论

当前实现已经具备功能闭环，但还没有形成真正的 DDD 模块边界。

主要问题不是“代码能不能运行”，而是：

1. 多个领域职责被放在 `executors`、`core`、`db` 等技术目录中，领域语言和技术实现混在一起。
2. 关键能力仍通过全局变量、模块级 registry 和 ambient context 传递，难以创建第二套独立实例。
3. Code Mode 直接依赖 workspace 和 shell 具体实现，替换存储、沙箱或工作区策略时 impact scope 过大。
4. 当前 StorageAdapter 是连接层 seam，不是完整的存储 bounded context；尚不能宣称已经支持 SQLite/PostgreSQL 可替换运行。
5. 测试主要验证单点行为，尚未验证多 Composition、多 Worker、热重载和失败恢复下的生命周期隔离。

架构评级：

| 维度 | 结论 |
|---|---|
| 功能完成度 | 良好 |
| DDD 分层 | 需要重构 |
| 依赖倒置 | 部分实现 |
| 可复用性 | 中等，受全局状态限制 |
| 安全边界 | Code Mode 存在 P1 缺口 |
| 独立模块化 | 尚未闭合 |

## 二、必须优先修复

### [P1] Code Mode 的 `tools.bash` 绕过了 Tool Executor 权限链

位置：

- `backend/executors/code_mode.py:141-155`
- `backend/executors/plugins/workspace_tools.py:1317`

`tools.bash()` 直接调用 `_handle_run_shell()`，只执行了 `_check_shell_command()` 和路径校验，没有重新经过 `tool_executor.execute()` 的：

- permission before hook；
- HIL / approval 决策；
- permission event recorder；
- after hook 的统一审计链。

`run_code` 自身虽然需要审批，但 Code Mode 内部可以批量执行多个 bash 命令，当前实现没有把每一条 bash action 作为独立的授权决策记录。

这违反项目已有的 ToolRouter / Tool Executor 安全原则。

建议：

1. Code Mode 只依赖 `BashPort`，不直接 import `_handle_run_shell`。
2. `BashPort.execute()` 内部必须调用统一的授权服务和 shell adapter。
3. 每一个 `tools.bash()` 调用生成独立的 action / audit record。
4. 未获得对应 action authorization 时，直接 fail-closed。

验收测试：

- Code Mode 内第二个 bash action 被 deny 时，第一个 action 的审计仍完整。
- HIL deny/timeout 不得进入 shell backend。
- Code Mode 不得通过 `tools.bash` 绕过 `run_shell` 的 permission hook。

### [P1] Code Mode 使用进程级 stdout/stderr 重定向

位置：`backend/executors/code_mode.py:189-194`

`contextlib.redirect_stdout()` 和 `redirect_stderr()` 修改的是进程级 `sys.stdout/sys.stderr`。当前执行虽然放在线程中，但 Worker 内其他线程在同一时间输出日志时，可能被捕获到 Code Mode 结果，或者输出丢失。

这不符合 Worker 多任务并发模型，也不是可复用的执行器设计。

建议：

- 将脚本执行放入独立 subprocess / container；
- 通过 pipe 收集 stdout/stderr；
- 由 adapter 层负责 timeout、memory、output limit 和 kill；
- application 层只消费 `CodeExecutionResult`。

当前 AST allowlist 只能作为第一层输入校验，不能作为真正的 Python 安全沙箱。

### [P1] StorageAdapter 仍不是完整的存储替换能力

位置：

- `backend/db/adapters.py:14-24`
- `backend/db/__init__.py:11-29`
- `backend/db/writer.py:94-101`
- `backend/core/patch_config.py:157-160`

当前实现已经把 `connect()`、`connect_sync()` 和 `write_connect()` 接到 adapter seam，这是正确方向；但仍有以下缺口：

- schema/migration 仍直接使用 SQLite SQL 和 SQLite DDL；
- 查询层默认假设 aiosqlite connection 行为；
- writer 的并发、事务和 lease 语义没有成为 adapter contract；
- adapter 没有 health check、migration capability、transaction capability 和 lifecycle；
- `nuke.patch.yml` 只能选择“已预先注册”的 adapter，尚没有生产可用 PostgreSQL adapter。

因此当前只能称为“可替换连接端口”，不能称为“可替换存储实现”。

建议定义完整端口：

```text
StoragePort
├── ConnectionPort
├── TransactionPort
├── WriterLeasePort
├── MigrationPort
├── HealthCheckPort
└── LifecyclePort
```

并将 SQL 方言、schema migration、retry/busy 策略放入各自 adapter。Application 不得判断 SQLite/PostgreSQL。

## 三、DDD / 依赖倒置问题

### [P2] Code Mode 把 Application、Domain、Infrastructure 混在一个文件

`backend/executors/code_mode.py` 同时包含：

- `CodeModeLimits`：策略/配置值对象；
- AST 安全规则：领域策略；
- `_SDK`：application facade；
- workspace 文件访问：infrastructure adapter；
- shell 执行：infrastructure adapter；
- `exec()` 生命周期：runtime adapter。

这导致任何一项改变都会修改同一个模块。

建议拆分为：

```text
backend/executors/code_mode/
├── domain.py       # CodeProgram, CodeModeLimits, CodeModePolicy
├── ports.py        # WorkspacePort, GrepPort, BashPort, OutputPort
├── application.py  # RunCodeService 编排 use case
├── validator.py    # AST/语法校验策略
└── adapters/
    ├── workspace.py
    ├── shell.py
    └── process.py
```

`RunCodeService` 只依赖 ports，不允许 import `workspace` 或 `workspace_tools`。

### [P2] IoC 容器注入发生在构造之后，且依赖全局容器

位置：

- `backend/executors/container.py`
- `backend/executors/base.py:220`
- `backend/executors/registry.py:10-22, 66-69`

目前流程是：

```text
instance = cls()
instance.dependencies = global_container.resolve_many(...)
instance.register_tools()
```

问题：

- 依赖没有进入构造函数，实例可能已经在无依赖状态下执行初始化逻辑；
- `BotExecutor.dependencies = {}` 是可变 class attribute；
- `_container` 是 process-global，无法表达 Worker/Composition scope；
- plugin 仍可绕过注入直接 import `db`、`workspace`、`core`。

建议：

```python
instance = plugin_factory(dependencies=container.scope(...))
```

并使用 immutable `DependencyScope`：

- Composition 创建 scope；
- Worker 拥有 Worker scope；
- Group/Session 可创建 child scope；
- plugin 只能从构造函数或显式 port 获取依赖。

同时移除 `dependencies = {}` class attribute，改成实例字段或 frozen dependency object。

### [P2] Disposer 依然是隐式全局注册机制

位置：`backend/executors/tool_executor.py:100-137, 280-289`

`registration_scope()` 通过 `_active_disposer` 全局变量捕获当前注册资源。这样做能解决一部分热重载泄漏，但仍存在：

- 并发 plugin registration 之间可能互相覆盖 active scope；
- `track_disposable()` 在没有 active scope 时静默 no-op；
- 资源所有权不在 plugin aggregate 内表达；
- 异步资源没有 `aclose()` 生命周期；
- `bus.on()` 创建的 background task 不由 `Subscription.close()` 管理。

建议：

- 使用 `contextvars.ContextVar[Disposer | None]`，而非模块级 `_active_disposer`；
- `track_disposable()` 没有 scope 时直接抛错或返回显式 unmanaged 状态；
- 支持 `close()` 和 `aclose()` 两种清理协议；
- Disposer 维护 `ResourceOwnership`，记录 owner/plugin/version；
- 事件 listener 的 task、queue、subscription 必须作为同一个 aggregate 一起销毁。

### [P2] `nuke.patch.yml` 位于 core，但直接依赖 infrastructure

位置：`backend/core/patch_config.py:157-160`

`core.patch_config` 直接 import `db.adapters.select_storage_backend`。这违反：

```text
Domain/Core → Port
Composition Root → Adapter
```

配置解析器应该只产出不可变的 `RuntimePatch`，由 composition root 决定如何把 patch 应用到具体 adapter。

建议：

```text
core/config/domain.py       # RuntimePatch value object
core/config/parser.py       # YAML parser + validation
composition/patch_apply.py  # apply RuntimePatch to concrete adapters
```

这样 core 不知道 db、shell、sandbox 的具体模块。

### [P2] 配置 patch 的“原子性”仍不完整

位置：`backend/core/patch_config.py:154-162`

虽然普通属性是在校验后批量写入，但 storage backend 已先执行全局选择，再写入其他 config 属性。如果后续新增属性 setter 或 adapter lifecycle 失败，系统可能出现：

```text
new storage backend + old runtime settings
```

建议先生成完整 `RuntimePatchPlan`，验证 adapter 可用、health、migration compatibility，再由 composition root 一次性 install；失败时恢复旧 binding。

## 四、模块目录与 bounded context 建议

目前目录按技术名组织：

```text
executors/code_mode.py
executors/tool_executor.py
executors/container.py
db/adapters.py
core/patch_config.py
bus/engine.py
```

建议形成明确的上下文：

```text
backend/runtime_features/
├── code_mode/
│   ├── domain.py
│   ├── application.py
│   ├── ports.py
│   └── adapters/
├── tool_runtime/
│   ├── domain.py
│   ├── application.py
│   ├── ports.py
│   └── adapters/
├── plugin_lifecycle/
│   ├── domain.py
│   ├── application.py
│   └── ports.py
├── configuration/
│   ├── domain.py
│   ├── parser.py
│   └── application.py
└── storage/
    ├── ports.py
    ├── registry.py
    └── adapters/
        └── sqlite.py
```

外部系统只依赖各 bounded context 的 `application` 或 `ports`，不直接依赖 adapters。

## 五、建议的重构顺序

### Commit 1：修复 Code Mode 安全边界

- `BashPort` + `AuthorizationPort`；
- 禁止 `_SDK` 直接调用 `_handle_run_shell`；
- 将执行移到 subprocess/container；
- 增加并发 stdout 隔离和取消测试。

### Commit 2：拆分 Code Mode bounded context

- 把 policy、SDK、执行器、workspace adapter 分开；
- `RunCodeService` 只依赖 ports；
- 保留现有工具 API 作为 facade。

### Commit 3：将 Disposer 改成 scoped lifecycle

- ContextVar scope；
- async disposer；
- plugin resource ownership；
- listener task 一起回收。

### Commit 4：将 IoC 改成 composition-scoped injection

- immutable `DependencyScope`；
- 构造函数注入；
- 删除 global container fallback；
- standalone host 必须显式提供 ports。

### Commit 5：完成 StoragePort

- migration/transaction/health/lifecycle contract；
- SQLite adapter 迁移到 `storage/adapters/sqlite.py`；
- 添加真正的 PostgreSQL adapter 或明确标记为未支持；
- patch 只生成 plan，由 composition root install。

## 六、最终验收标准

重构完成前，不建议宣称“独立可复用模块”。至少需要满足：

- Code Mode 不 import 当前项目的 workspace/shell 具体实现；
- 任意两个 Memory/Runtime Composition 可以并存而不共享全局 registry；
- plugin reload 后旧 tool、hook、middleware、event task、adapter 全部消失；
- 未配置依赖时 standalone host 在 composition 阶段 fail-fast；
- storage backend 选择不会静默回退 SQLite；
- 所有跨层调用通过 port；
- 所有写入/执行 use case 都有明确 aggregate owner 和生命周期；
- 真实多 Worker、并发 reload、权限拒绝、adapter 故障测试全部通过。

## 结论

这 4 组提交完成了“能力原型”和“第一层安全护栏”，但还没有完成 DDD 意义上的独立模块化。

最严重的问题是 Code Mode 的 bash 子调用绕过统一授权链，以及 `exec + redirect_stdout` 不具备可靠的 Worker 隔离语义。其次是 IoC、Disposer、StorageAdapter 都仍依赖 process-global registry。

建议先修复 P1，再进行目录重构；不要继续在现有 `executors/*.py`、`core/*.py`、`db/*.py` 顶层追加功能，否则 impact scope 会继续扩大。
