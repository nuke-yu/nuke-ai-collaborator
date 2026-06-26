# 外部 / 全局 Skill：识别、自动加载、分配给 Bot

**日期**：2026-06-27
**分支**：`design/skill-layer-role-binding`
**状态**：设计已确认，待写实现计划

---

## 1. 目标

让 Bot 能使用**外部 skill**。三项能力：

1. **识别**——把外部 skill 导入系统，进入全局池。
2. **自动加载**——被分配的全局 skill 自动进入 Bot 的 prompt / tool loop，与现有各层 skill 同等流转。
3. **分配**——在 UI 上把全局 skill 分配给指定 Bot。

来源已定：**仅 git URL 导入**（不做 zip 上传 / 不做宿主机 `~/.claude` 复用）。

---

## 2. 前置事实与边界（务必先读）

- **Skill 在服务端执行**：在 backend 主机的 per-group 沙箱里（生产 Linux，dev Mac），过 `run_shell` guard。Bot 永远不在用户机器上跑。
- **无法改写第三方 skill 正文**：若 `SKILL.md` 让模型「run `./scripts/foo.sh`」，没有 loader 能让它在 Windows 上跑。因此 portability 的做法是 **分类 + 门禁 + 对可控部分提供可移植契约**，而非「魔法移植任意 skill」。
- **依赖项（不在本次范围内解决）**：现有沙箱（`NUKE_SHELL_EXEC_BACKEND=container`，bwrap/Docker）是**仅 Linux** 的。Windows backend 没有 bwrap、Docker 形态也不同——**Windows 上的 shell-exec 沙箱是已存在的缺口**，本工作依赖它，但不解决它。
- **群组隔离不破**：skill 是指令文本、不是群组数据，全局池跨群组共享 skill **定义**不违反群组隔离（bots / memory / 对话历史仍隔离）。`system` 层本来就是跨群组的。

---

## 3. 数据模型

### 3.1 全局池 = 现有 `system` 层
- 物理位置：`workspaces/system/skills/<name>/`。
- 导入的外部 skill 落在这里，跨群组可见。
- `_merge_skill_entry` 的 **A1 system 保护**仍然生效（system skill 不可被下层遮蔽）。

### 3.2 Skill 单元 = 文件夹（现状，无需改）
- `<name>/SKILL.md`（frontmatter 为索引）+ 附带 `scripts/`、`helper.py`、`references/`。
- `scan_dir` 已把 `<name>/SKILL.md` 识别为 skill；`SkillStore.copy` 用 `copytree` 整体搬运并做 symlink 逃逸检查。无需逐文件逻辑。

### 3.3 分配 = per-bot 清单文件
- 位置：现有 `BotScope` 目录 `bot_dir/skills/manual/_assigned.json`。
- 内容：`["pdf-extract", "code-review", ...]`——按**名字**引用全局池，不复制内容。
- 分配 / 取消 = 重写一个 JSON；契合现有 mtime-signature 缓存（清单 mtime 变化即失效）。

---

## 4. 识别：导入与校验

**接口**：`POST /api/skills/import`（token 鉴权，DFT-082），body `{ "git_url": "...", "ref": "main" }`。

**流水线**（全程在临时目录，校验通过前不碰全局池）：

1. **拉取**——`git clone --depth 1 --branch <ref> <git_url>` 到 temp。
2. **条目消毒**——
   - 拒绝逃逸根目录的路径（`..`、绝对路径、Windows 盘符 `C:`）。
   - 归一化分隔符 `\` → `/`（Windows 仓库）。
   - 拒绝逃逸 symlink（复用 `store.copy` 的 `is_relative_to` 检查）。
3. **形态检查**——必须存在 `SKILL.md`；`parse_skill_meta` 解析；`name` / `description` 缺失或 `name` 不过 `_is_safe_name` → 拒绝。支持仓库内含**多个** skill 文件夹时逐个导入。
4. **归一化**——去 BOM；`.sh` 的 CRLF → LF（让 Linux shebang 生效）；保留 `.ps1` 的 CRLF。
5. **安全诊断**——扫 `HIGH_PRIVILEGE_TOOLS`（复用 `store.write` 的子串扫描逻辑），返回 `high_privilege` 标记 → operator 确认前 UI 警告。
6. **可移植性分类**（见 §5）→ 写入 skill 的 `platforms` 字段。
7. **落盘**——原子移动进 `system/skills/<name>/`；mtime 变化使缓存失效，`SkillWatcher` 自动捡起。
8. **重名处理**——拒绝或加版本后缀（实现期定），结果回传 UI。

**返回**：`{ imported: [{name, platforms, high_privilege}], rejected: [{path, reason}] }`。

---

## 5. 可移植性策略（Linux + Windows backend）

backend 本身可能跑在 Linux 或 Windows，bundled 脚本必须两边都能应付。我们不能改写第三方正文，因此 **分类 + 门禁 + 可控部分的可移植契约**：

- **导入时分类**（写入 skill 的 `platforms`）：
  - `pure`——仅 md / py，到处能跑。
  - `posix`——带 `.sh`。
  - `windows`——带 `.ps1` / `.cmd`。
  - `cross`——两者都有 / frontmatter 显式声明。
- **主机能力**：backend 暴露 `platform.system()`。
- **加载时门禁**：新 source 丢弃 / 标记 `platforms` ⊅ 主机 OS 的 skill，通过现有 A5 / C1 / C2 诊断通道发警告——**绝不**把坏 skill 静默喂给模型。
- **分配时 UX**：面板显示可移植性徽章；在 Windows 主机上分配 `posix`-only skill 会被警告 / 拦截。
- **路径可移植**：加载时注入绝对、OS 正确的 `${SKILL_DIR}`，让 skill 正文写 `python ${SKILL_DIR}/scripts/foo.py` 而非硬编码相对路径 / 分隔符。
- **钦定可移植形态**：**Python helper**——两个 OS 上 runtime 本来就是 Python。导入文档与 UI 引导优先 Python helper。

---

## 6. 自动加载：第 5 个 source

### 6.1 新 source
- 新建 `backend/skills/sources/manual.py` → `BotManualSource(ctx)`：
  - 读 `bot_dir/skills/manual/_assigned.json`。
  - 每个名字解析回全局池 `system/skills/<name>/`，返回 `SkillEntry`（`layer="manual"`）。
  - 施加 §5 加载时门禁（不兼容 → 丢弃 / 诊断）。
  - `signature()` = 清单文件 mtime + 被引用池目录的 `dir_signature`。

### 6.2 接入扫描
- `discovery._sources()` 增加第 5 个元素 `BotManualSource`。
- `discovery._scan_signature()` 把 manual source 的 signature 并入。
- `merge_layers` 签名扩展，把 `manual` 列表插入覆盖链。

### 6.3 覆盖顺序（已确认）

层层覆盖，后者盖前者，`system` 受保护。`manual` 插在 `role` 与 `learned` 之间：

```
system  <  group  <  role  <  manual  <  learned.active  <  learned.personal
```

- 含义：显式分配的全局 skill 盖过 role / group / system 默认；但 Bot 自己 learned / personal 的细化仍然最高。
- 落地：`_LAYER_ORDER` 增加 `"manual"`，置于 `role`(2) 与 `learned`(3) 之间（重排数值）；`merge_layers` 在 `role` 循环后、`learned` 循环前应用 `manual`。

> 用户口径：理论上每层 skill 独立；偶有放错位置时，加载覆盖顺序就是从 learned 一层层往下覆盖。本顺序与之一致。

---

## 7. 分配：接口 + UI

### 7.1 接口（group-scoped，对齐现有 `members` 寻址）
- `GET /api/groups/{gid}/members/{bot_id}/skills`
  → `{ assigned: [name...], pool: [{name, description, platforms, high_privilege}...] }`
- `PUT /api/groups/{gid}/members/{bot_id}/skills`
  body `{ assigned: [name...] }` → 校验每个 name 存在于全局池且 `_is_safe_name`，重写 `_assigned.json`。

> 复用 `BotScope`（`bot:{gid}:{bot_id}`）解析定位 bot 目录；路径安全仍全部落在 `scope.parse_descriptor`。

### 7.2 UI（Bot 配置「技能」面板）
- 列全局池（`GET /api/skills?scope=system`）：description + 可移植性徽章 + 高权限警告。
- 显示当前分配；开关 toggle → `PUT .../skills` 重写清单。
- 「导入 skill」按钮 → 填 git URL → 驱动 §4，完成后刷新池。

---

## 8. 受影响文件清单（实现期参考）

| 文件 | 改动 |
|---|---|
| `backend/skills/sources/manual.py` | 新建 `BotManualSource` |
| `backend/skills/discovery.py` | `_sources()` / `_scan_signature()` 加第 5 source |
| `backend/skills/composer.py` | `merge_layers` 签名 + `_LAYER_ORDER` 加 `manual` |
| `backend/skills/importer.py`（新） | git 拉取、消毒、归一化、分类、落盘 |
| `backend/skills/portability.py`（新） | `platforms` 分类 + 主机 OS 门禁 + `${SKILL_DIR}` 注入 |
| `backend/api/skills.py` | `POST /api/skills/import` |
| `backend/api/groups.py` | `GET/PUT /api/groups/{gid}/members/{bot_id}/skills` |
| frontend Bot 配置面板 | 技能分配 + 导入 UI |

---

## 9. 测试要点（实现期）

- **导入消毒**：`..` / 绝对路径 / 盘符 / 逃逸 symlink 全部拒绝。
- **可移植性分类**：`pure` / `posix` / `windows` / `cross` 四类判定正确。
- **加载门禁**：`posix` skill 在模拟 Windows 主机上被丢弃并产生诊断。
- **第 5 source 覆盖顺序**：`manual` 盖过 `role`，被 `learned` 盖过；signature 缓存对清单增删改敏感。
- **分配接口**：`PUT` 写入不存在 / 不安全 name 被拒；清单重写正确。
- **群组隔离**：bot A 的分配不影响 bot B；全局池 skill 定义跨群组可见但分配 per-bot。

---

## 10. 未决 / 实现期再定

- 重名导入策略：拒绝 vs 版本后缀。
- `${SKILL_DIR}` 注入的具体载体（prompt 模板变量 vs 加载时正文替换）。
- frontmatter 显式声明 `platforms` 的 schema 细节。
