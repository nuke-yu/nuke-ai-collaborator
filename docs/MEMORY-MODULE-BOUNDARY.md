# Memory 模块独立化边界

## 目标

`backend/memory` 是独立的 Memory bounded context。外部项目只能依赖：

- `memory.contracts`：命令、结果和版本契约；
- `memory.domain`：领域规则与安全原语；
- `memory.ports`：数据库、投影、算法和应用服务端口；
- `memory.composition`：由宿主完成具体装配。

业务代码不得直接依赖 SQLite、workspace、FastAPI、AI provider 或当前项目的全局数据库。

## 当前已完成的边界

- application 层不再直接从 `memory.infrastructure` 导入数据库实现；
- 安全处理使用纯 domain safety primitives；
- 数据库通过 `MemoryDatabasePort` 注入；
- 函数式入口的默认解析集中在 `memory.application.context`，而不是散落在 use case 中；
- standalone host 可以通过 `configure_database()` 和 `configure_service()` 注入自己的实现；
- 生产 SQLite、Chroma 和当前项目运行时只由 composition/infrastructure 选择。

## 仍需继续收敛的边界

以下依赖仍属于宿主适配层迁移项，不得扩散到新的 application use case：

- 中央成员目录与 ACL 查询；
- AI model call 与运行时配置；
- Skill workspace 文件投影；
- Personal Vault cursor signer 和中央删除审计；
- Chroma client 与当前项目的 executor redaction。

这些能力应分别转换成 host port，由 `memory.bootstrap` 或外部项目的 composition root 注入。

## 依赖方向

```text
host composition root
        ↓ inject
memory adapters / infrastructure
        ↓ implement
memory ports
        ↑ depend
memory application
        ↑ depend
memory domain / contracts
```

## 独立迁移标准

Memory 才能被复制到其他项目，当且仅当：

1. `memory.application` 不再导入 `memory.canonical`、项目 `db`、workspace、AI 或业务模块；
2. 所有外部副作用都有对应 port；
3. 新项目只需提供 adapters 和 composition，不需修改领域/application 代码；
4. standalone contract tests 不启动 FastAPI、不依赖当前项目数据库；
5. SQLite、Chroma 和当前项目适配器可以替换而不改变 `memory.contracts`。
