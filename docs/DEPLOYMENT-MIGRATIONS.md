# 部署数据迁移 / Deployment Data Migrations

集中记录**一次性数据迁移脚本**——这些不是 schema 迁移（`db/migrations.py` 在启动时自动跑），
而是需要在部署/升级时**手动执行**的数据修复或重组脚本。全部从 `backend/` 目录运行。

> Schema 迁移（`ALTER TABLE` 等）由 `db/migrations.py` 的 `run_migrations` 在启动时自动、幂等地执行，不在此列。

约定：脚本放在 `backend/scripts/`，尽量提供 `--dry-run`，且**幂等**（可重复运行）。

---

## 1. Chroma 记忆时间戳回填 — `scripts.backfill_chroma_timestamps`

## Chroma 版本兼容、备份与安全重建

Chroma 是由 canonical SQLite 记录派生的索引，但迁移工具仍必须在应用停机后执行。
`rebuild_chroma_fact_ids` 会在任何写入前读取 SQLite migration metadata，确认指定
Chroma runtime 版本，并自动完整复制索引目录；无法读取或缺少 migration metadata 时会拒绝
写入。备份路径会打印到 stdout，可直接恢复为原目录。

```bash
# 在 backend/ 目录、应用已停止的前提下
python3 -m scripts.rebuild_chroma_fact_ids --dry-run \
  --chroma-path /var/lib/nuke-ai-collaborator/chroma_db \
  --expected-chroma-version 1.5.9

# 自动备份后迁移幸存的 legacy IDs
python3 -m scripts.rebuild_chroma_fact_ids \
  --chroma-path /var/lib/nuke-ai-collaborator/chroma_db \
  --backup-dir /var/backups/nuke-chroma

# 旧格式无法安全读取或历史 ID 覆盖导致数据丢失时：从 canonical SQLite 重建
python3 -m scripts.rebuild_chroma_fact_ids --rebuild --group-id 3 \
  --chroma-path /var/lib/nuke-ai-collaborator/chroma_db
```

当前实现状态：上述兼容检查、自动备份、旧库隔离和 canonical SQLite 全量重建已经完成。
`--rebuild` 遇到无法读取的旧格式时不会打开旧 Chroma，而是将其保留为
`*.pre-rebuild-*` 目录，再创建新索引。恢复或清理这些备份前，请先完成离线校验。

运行时使用 `NUKE_CHROMA_PATH`（默认 `./chroma_db`）。所有相关命令须指向同一路径。
`--dry-run` 不创建备份、不做写入；真正执行时不能跳过备份。

## Rootless Docker sandbox daemon

生产容器沙箱必须连接到专用的 rootless Docker daemon，不能挂载宿主机
`/var/run/docker.sock`。启动 rootless daemon 后，将它的 user socket 暴露给 Compose：

```bash
export NUKE_ROOTLESS_DOCKER_SOCKET=/run/user/$(id -u)/docker.sock
docker compose up -d --build
```

Linux 主机可将 `deploy/nuke-rootless-docker.service` 安装为 Docker 用户服务，并在启动
Compose 前执行 `deploy/check-rootless-docker.sh`。检查脚本会拒绝空 socket、host root
socket，以及未报告 rootless 安全选项的 daemon。

生产部署还必须设置 `NUKE_DOCKER_PROXY_IMAGE` 和 `NUKE_SANDBOX_IMAGE`，值应为固定版本
或 digest，不能使用 `latest`。rootless Docker 安装应确保 `dockerd-rootless.sh` 位于
`/usr/bin/`，否则请在 service 文件中改为发行版实际路径并执行 `systemctl --user daemon-reload`。

Compose 仅将该 socket 交给 API-minimizing proxy；应用仍只连接私有的
`tcp://docker-proxy:2375`。生产启动校验要求 `NUKE_DOCKER_ISOLATION=rootless`，并拒绝
host-root socket。使用 systemd 时，在 `/etc/nuke-ai-collaborator/env` 中设置
`DOCKER_HOST=unix:///run/user/<nuke-uid>/docker.sock`；该 daemon 的用户必须拥有数据目录及
各 group workspace，才能创建受限的 bind mount。

## Worker CJK tokenizer 校准

可选地为某个 provider/model 提供本地 HuggingFace `tokenizer.json`。Worker 在启动前完成
校准，不访问网络，也不会让单个 tokenizer 加载失败中断启动：

```bash
export NUKE_TOKENIZER_PATHS_JSON='{"openai/gpt-4o":"/opt/tokenizers/gpt-4o/tokenizer.json"}'
```

校准结果按 `provider/model` 保存在 `NUKE_TOKENIZER_CALIBRATION_PATH` 指定的 JSON 文件及
Worker 进程内，并通过
`nuke_memory_tokenizer_abs_error_avg` 和 Worker 快照中的
`tokenizer_configured_models` 暴露。应将 tokenizer 文件作为部署制品版本化，并在升级模型
或 tokenizer 后重启 Worker。

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
