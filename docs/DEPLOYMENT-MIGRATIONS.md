# 部署数据迁移 / Deployment Data Migrations

集中记录**一次性数据迁移脚本**——这些不是 schema 迁移（`db/migrations.py` 在启动时自动跑），
而是需要在部署/升级时**手动执行**的数据修复或重组脚本。全部从 `backend/` 目录运行。

> Schema 迁移（`ALTER TABLE` 等）由 `db/migrations.py` 的 `run_migrations` 在启动时自动、幂等地执行，不在此列。

约定：脚本放在 `backend/scripts/`，尽量提供 `--dry-run`，且**幂等**（可重复运行）。

---

## 1. Chroma 记忆时间戳回填 — `scripts.backfill_chroma_timestamps`

**何时需要**：记忆 recency 衰减重构（commit `14461da`，2026-06-13）之后。在此之前写入的
Chroma 记忆没有 `timestamp` 元数据，检索排序器 `TimeDecayRanker` 会把它们当作约 30 天前的旧
数据、压到结果底部。

**作用**：按每条记忆的 `group_id` 元数据，回到对应 group DB 读取源消息的 `created_at`，写回为
UTC epoch（与 `add_to_chroma` 写入新记忆时的 `time.time()` 同基准）。

```bash
# 先停应用，避免回填期间有写入
python3 -m scripts.backfill_chroma_timestamps --dry-run   # 只报告，不写
python3 -m scripts.backfill_chroma_timestamps             # 实际写回
```

**特性**：幂等（已有 timestamp 的记录跳过，可重复跑）；低风险。
缺少 `group_id` 的遗留记录无法定位所属 group DB（群消息 id 各群独立、会冲突），退而对中央库做
best-effort 查询，多数仍保留默认衰减——可接受，因为它们确实是旧记忆。

输出统计：`scanned` / `missing timestamp` / `updated` / `no group_id` / `skipped (no msg)`。

---

## 2. 工作区目录布局迁移 — `scripts.migrate_workspace_layout`

**何时需要**：工作区布局重构合并后（见 [`WORKSPACE-LAYOUT-DESIGN.md`](WORKSPACE-LAYOUT-DESIGN.md)）。

```bash
# 先停应用并备份工作区目录
python3 -m scripts.migrate_workspace_layout --apply
```

把旧布局的群组目录迁移到新的 `group_{id}/{bots,shared,...}` 结构。**务必先停应用 + 备份**。
详细动机与目标结构见设计文档。

---

## 3. 向量索引重建 — `scripts.reindex_embeddings`（DFT-035）

**何时需要**：更改 `NUKE_EMBEDDING_PROVIDER` / `NUKE_EMBEDDING_MODEL` 之后。旧向量是用上一个
模型构建的，维度不兼容，`ai/memory.py` 会拒绝加载。

```bash
# 先停应用
python3 -m scripts.reindex_embeddings --dry-run   # 显示目标模型/签名
python3 -m scripts.reindex_embeddings             # 用新模型重新嵌入并重盖集合签名
```

用当前配置的模型重新嵌入所有已存文档，并重盖集合的 `emb_sig` 签名。
