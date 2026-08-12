# Memory 新旧体系架构对比

日期：2026-08-12

## 结论

当前项目同时存在旧 Memory 体系和正在建设中的新 Memory 体系。最终目标不是长期双轨运行，而是由 `backend/memory` 成为唯一的 Memory 业务实现，旧 `backend/ai` Memory 模块在迁移完成后删除。

## 新旧体系对比

| 维度 | 旧 Memory 体系 | 新 Memory 体系 |
|---|---|---|
| 核心位置 | `backend/ai/*` | `backend/memory/*` |
| 主要入口 | `ai.memory`、`ai.personal_vault`、`ai.experiences` | `memory.application` |
| 架构风格 | 业务逻辑、数据库、Chroma、LLM 调用混合 | Domain / Application / Ports / Infrastructure 分层 |
| 事实来源 | Chroma 和 SQLite 并存，存在双写 | canonical SQLite 作为唯一事实源 |
| 向量库定位 | 同时参与存储和主流程判断 | 目标是降级为 SQLite 的派生 projection |
| 数据写入 | 业务函数直接操作数据库或 Chroma | Application Service → Repository → Transaction → Outbox |
| 事务模型 | 各函数自行连接数据库和提交事务 | 目标是统一 Unit of Work，并与 Outbox 同事务 |
| 投影机制 | 旧逻辑中直接写 Chroma，并带有 fallback | 通过持久化 Outbox 异步投递 projection |
| 授权方式 | 依赖调用方传入 `user_id`、`group_id`、`bot_id` | 使用 `Principal`、`MemoryScope` 和 ACL |
| Group 隔离 | 主要依赖 SQL 条件和数据库路径 | Scope、Principal、ACL、物理 group DB 多层隔离 |
| Personal Memory | `ai.personal_vault` 直接处理数据库和 app 状态 | `AuthorizedPersonalKnowledgeService` 作为授权边界 |
| Group Fact | 没有完整独立的 canonical Group Fact 服务 | `GroupFactService` 负责规范化、状态和 FTS 召回 |
| Bot Fact / Reflection | 主要写入 Chroma | canonical SQLite 记录加 projection outbox |
| 关系图 | 偏向向量关联和临时推断 | `CanonicalRelationService` 负责 group 内关系和有界遍历 |
| 学习系统 | `ai.pipeline`、`ai.experiences`、`ai.skill_learning` 分散实现 | 目标迁入 `memory.application.learning` |
| 算法职责 | 算法和业务流程耦合 | 算法放在 `memory/adapters/algorithms`，目标是纯计算和决策 |
| 安全脱敏 | 主要依赖 executor 链路脱敏 | canonical Memory 写入前统一脱敏、截断和限制嵌套 |
| 运行时依赖 | 依赖 `ai.memory` 全局函数和全局数据库 | 通过 `MemoryComposition` 显式组装依赖 |
| 全局状态 | `_memory_db`、Chroma 全局 Store 等较多 | 目标是逐步移除全局状态，改用显式依赖注入 |
| API 形态 | 函数式 API，参数容易缺失或绕过授权 | Command / Query / Port 契约 |
| 错误处理 | 各模块自行 fallback，行为不统一 | Application 层统一权限和领域错误 |
| 测试方式 | 直接测试旧函数和具体存储实现 | 测试契约、ACL、隔离、事务、projection 和算法 |
| 生命周期 | 旧模块随业务调用初始化 | `MemoryModule` 独立管理 schema、outbox 和 reconcile |

## 核心架构差异

旧体系：

```text
业务函数 → Chroma / SQLite
```

新体系：

```text
Application Service
        ↓
Domain / ACL
        ↓
Canonical SQLite
        ↓
Transactional Outbox
        ↓
Chroma 等派生索引
```

## 当前迁移状态

新体系已经具备以下能力：

- 显式 `MemoryComposition`
- Group Fact canonical 服务
- Bot Fact 和 Reflection canonical 持久化
- Projection Outbox
- Principal / MemoryScope / ACL 授权模型
- Memory 写入前脱敏、长度限制和嵌套深度限制
- 新旧依赖 import boundary 检查

以下旧模块仍然存在，但已从 lifecycle 的生产学习链路中移除；它们目前只承担兼容、历史数据回填或旧测试职责：

- `backend/ai/memory.py`
- `backend/ai/personal_vault.py`
- `backend/ai/experiences.py`
- `backend/ai/skill_learning.py`
- `backend/ai/pipeline.py`
- `backend/ai/cases.py`
- `backend/ai/usage_tracking.py`

因此核心生产链路已经切换到新体系；剩余工作是清理兼容/回填入口，并在对应测试迁移后删除旧实现。

## 最终目标

1. `backend/memory` 成为唯一 Memory 业务实现。
2. SQLite canonical records 成为唯一事实源。
3. Chroma、向量索引和其他搜索系统全部变成派生 projection。
4. 所有生产入口统一经过 Memory application ports。
5. 删除 `memory/adapters/runtime/*_legacy.py`。
6. 删除对应的 `backend/ai` Memory 业务模块。
7. 不保留两个可独立运行的 Memory 体系。

## 迁移顺序

```text
Group/Bot Memory
    ↓
Projection
    ↓
Personal Vault
    ↓
Learning
    ↓
Conversation Memory
    ↓
删除全部 legacy 实现
```

## 迁移原则

- 旧实现只允许作为临时迁移来源，不再新增业务功能。
- 新代码禁止依赖旧 Memory 业务模块。
- 每迁移一个子域，必须补齐契约测试、跨 Group 隔离测试和失败恢复测试。
- 只有在生产调用链切换完成、回归测试通过后，才能删除对应旧实现。
- 兼容层是迁移工具，不是最终架构的一部分。
