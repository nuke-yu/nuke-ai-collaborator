# 外部 / 全局 Skill：识别、自动加载、分配给 Bot（产品级）

**日期**：2026-06-27（rev3：产品级重修——分配改一等 `bot_skills`、导入改注册表+版本溯源、external 两层、迁移一等动作）
**分支**：`design/skill-layer-role-binding`
**状态**：设计已确认，待写实现计划
**定位**：面向**产品级 agent**，非临时方案——数据模型预留生命周期/版本/审计；能力(分配)与安全(执行审批)分离。

---

## 0. 参考实现（已读源码）

- **OpenCode**（`/Users/Nuke/opencode/packages/opencode/src/{skill,tool/skill.ts,config/skills.ts}`）：扁平池 + 远程 `index.json` 拉取 + `available(agent)` 用权限过滤可见性。
- **Claude Code**（`/Users/Nuke/claude-code-haha-main/src/{tools/SkillTool,skills/loadSkillsDir.ts,utils/frontmatterParser.ts}`）：skill=slash-command；inline/fork（fork=真子 agent `runAgent`）；`${CLAUDE_SKILL_DIR}` + base-dir 头 + Windows 反斜杠归一化；权限规则 + safe-properties 自动放行；`shell: bash|powershell` 作者声明可移植性；不可信来源禁内联 shell；`addInvokedSkill` 压缩保活。

两套都是单用户/单进程，其分配/扁平/松散继承是「单用户最优」。本设计**只采纳其执行层精炼处**，分配/隔离/权限按产品级多租户重做。

### 0.1 三方对比与取舍

| 维度 | 我们（现状） | OpenCode | Claude Code | 取舍 |
|---|---|---|---|---|
| 执行层 tool | `run_skill`（base-dir+`${SKILL_DIR}`+参数替换+companion+fork） | 注入正文+文件列表 | inline/fork+`${CLAUDE_SKILL_DIR}` | **保留我们的**+补 Windows 归一化、companion 上限 |
| inline/fork | ✅ | ❌只 inline | ✅（fork=真子 agent） | inline 保留；**fork 升级真子 agent** |
| 跨平台 | `template` Jinja OS 变量 | 无 | `shell:` 作者声明+路径归一化 | **采纳作者声明**，废主机门禁 |
| 分配/可见性 | （原计划清单/权限） | 权限规则 | 权限规则+safe-props | **改一等 `bot_skills`**（产品级：能力≠权限，见 §3.3） |
| 分层 merge | 4 层+A1 | 扁平 | 扁平 | **保留分层**，external 作两层插入 |
| 导入 | 无 | `index.json` registry | `_canonical_` 远程+缓存 | **git 导入+注册表+版本溯源**（§4） |
| 压缩保活 | micro-compact | — | `addInvokedSkill` | **采纳** |

---

## 1. 目标与能力映射

让 Bot 以**产品级**方式使用外部 skill：

| 能力 | 实现 |
|---|---|
| 识别 | `POST /api/skills/import`：git clone → 校验 → 落两层池 + 写**注册表**（source/ref/commit/version/importer） |
| 自动加载 | `ExternalPoolSource`（全局+群组两层）接入扫描；可见性由 **`bot_skills.enabled`** 过滤 |
| 分配 | 一等 **`bot_skills`** 表（capability）；执行审批走 name-scoped `permission_rules`（security） |
| 生命周期 | 注册表支持 import/remove（v1）+ update/pin/audit（数据模型预留） |

---

## 2. 前置事实与边界

- **Skill 服务端执行**：backend 主机 per-group 沙箱（生产 Linux，dev Mac），过 `run_shell` guard。
- **`run_skill` 执行层已就绪且最全**：base-dir 头（loader.py:133）+ `${SKILL_DIR}` + 参数替换 + companion（:137）+ inline/fork（:155）。本设计不重写执行层基座，只增强（§7.5）。
- **`${SKILL_DIR}` 已实现**（非未决项）。
- **依赖项（不解决）**：沙箱 `NUKE_SHELL_EXEC_BACKEND=container` 仅 Linux；Windows backend 的 shell-exec 沙箱是已存在缺口，本工作依赖但不解决。
- **群组隔离**：全局池是跨群组 skill **定义**；群组池**仅本群可见**；**分配 per-bot**。bots/memory/历史仍隔离。

---

## 3. 数据模型

### 3.1 External 池 = 两层（全局 + 群组）
- **全局 operator 池**：`workspaces/external/skills/<name>/`（`layout.external_global_skills_dir()`），平台管理员策展，跨群组。
- **每群组池**：`group_{gid}/external/skills/<name>/`（`layout.group_external_skills_dir(gid)`），群管理员导入，**仅本群可见**。
- 均**默认 opt-in**：bot 只有在 `bot_skills.enabled` 命中时才看得到/调得到。
- 现有 `system / group / role / learned` 四层**行为不变**。

### 3.2 Skill 单元 = 文件夹（现状，无需改）
- `<name>/SKILL.md`（frontmatter 为索引）+ `scripts/`、`helper.py`、`references/`。
- `scan_dir` 已识别目录 skill；`SkillStore.copy` 用 `copytree` + symlink 逃逸检查。

### 3.3 分配 = 一等 `bot_skills`（能力），与执行审批分离
**产品级核心决策：capability ≠ permission，拆两张表。**

**(A) 分配（capability，真相源）—— 新表 `bot_skills`**
```
bot_skills(
  id, bot_id, skill_name,
  pool        TEXT,     -- 'external_global' | 'external_group'
  enabled     INTEGER DEFAULT 1,
  assigned_by INTEGER,  -- operator user id（审计）
  assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(bot_id, skill_name)
)
```
- 「这个 bot 拥有哪些外部技能」的唯一真相源；有状态（enabled/disabled）、可审计（谁/何时分配）。
- **可见性过滤**：external 层条目仅当 `bot_skills(bot_id, name).enabled=1` 才进 bot 的技能菜单（§6.3）。

**(B) 执行审批（security）—— 现有 `permission_rules`，仅管 HIL**
- 不再用 permission_rules 表达「分配」。它只回答「这个已分配技能被调用时，放行 / ask / deny」。
- **修 name-scoped 匹配**：run_skill 的规则只匹配 `name` 字段，**不**递归全参数（现状 `args_pattern` 递归 fnmatch 任一参数值，`run_skill(name="build", args="deploy …")` 会误中——产品级不可接受）。
- 默认：普通已分配技能 → allow；声明 `HIGH_PRIVILEGE_TOOLS` 的 → ask（safe-properties fail-safe，借 Claude Code：未知高权 frontmatter 属性默认要审批）。

> 分离带来的产品能力：可表达「已分配但暂时禁用」「已分配但每次仍审批」，且分配动作可审计——寄生 permission_rules 都做不到。

### 3.4 导入注册表 —— 新表 `external_skills`（溯源 + 生命周期）
```
external_skills(
  id, name,
  scope_kind  TEXT,     -- 'global' | 'group'
  group_id    INTEGER,  -- scope_kind='group' 时有效
  source_url  TEXT, ref TEXT, commit_sha TEXT,   -- 溯源
  version     TEXT,     -- 取自 SKILL.md frontmatter
  platforms   TEXT,     -- pure/posix/windows/cross（UI 提示）
  high_privilege TEXT,  -- 命中的高权工具列表
  imported_by INTEGER, imported_at TIMESTAMP,
  status      TEXT DEFAULT 'active',   -- active | disabled
  UNIQUE(scope_kind, group_id, name)
)
```
- 文件是内容真相源（scanner 读盘）；注册表是**溯源 + 生命周期**真相源。
- import 同时写「文件 + 注册表行」；remove 同时删两者；二者一致。
- v1 实现 import/remove；update（re-pull 新 commit）、pin、audit 列**数据模型现在就预留**，避免后续迁移。

---

## 4. 识别：导入注册表

**接口**：
- `POST /api/skills/import`（管理员级 token）body `{ git_url, ref, scope: "global" | {group_id} }`。
- `DELETE /api/skills/external/{id}`（remove）。
- 预留：`POST /api/skills/external/{id}/update`（re-pull）、`/pin`。

**导入流水线**（全程临时目录，校验通过前不碰池）：
1. **拉取**——`git clone --depth 1 --branch <ref> <url>`；记 `commit_sha`。**安全护栏**：clone size/时长上限；host 白名单（可配置，生产收紧，默认放开内网+github）。
2. **条目消毒**——拒绝逃逸路径（`..`/绝对/盘符），归一化 `\`→`/`，拒绝逃逸 symlink（复用 `store.copy`）。
3. **形态检查**——必须有 `SKILL.md`；`parse_skill_meta`；`name`/`description` 缺失或 `name` 不过 `_is_safe_name` → 拒绝；仓库多 skill 逐个导。
4. **归一化**——去 BOM；`.sh` CRLF→LF；保留 `.ps1` CRLF。
5. **不可信收紧**——导入 skill 视为**不可信**：默认**禁内联 shell 注入**（`!\`cmd\``/```! ``` 不在导入 skill 上执行，镜像 MCP-skill 规则）；扫 `HIGH_PRIVILEGE_TOOLS` 标记。
6. **可移植性分类**（§5）→ `platforms`。
7. **落盘 + 写注册表**——原子移动进对应池目录；写 `external_skills` 行（source/ref/commit/version/importer）；mtime 变化 → 缓存失效，`SkillWatcher` 捡起。
8. **重名**——**拒绝 + UI 提示**（同 scope 内 `UNIQUE` 约束）。

**返回**：`{ imported: [{id, name, version, platforms, high_privilege}], rejected: [{path, reason}] }`。

---

## 5. 可移植性策略（Linux + Windows backend）—— 作者声明，不按主机门禁

- **`shell: bash | powershell` frontmatter**（借 Claude Code）：声明内联块用哪个 shell；文件级；不读 host defaultShell——「作者挑 shell，不是读者」。
- **复用现有 `template:true` Jinja**（loader.py:121-130）：`{{ os }}/{{ is_windows }}/{{ shell }}`，作者按 OS 分支。
- **`${SKILL_DIR}` Windows 反斜杠归一化**（借 loadSkillsDir.ts:361）：Windows 下 base dir `\`→`/`。**run_skill 需补的一小处。**
- **`platforms` 仅 UI 徽章**（`pure/posix/windows/cross`）——**不硬门禁**；Windows 主机分配 `posix`-only skill 时 UI **警告**不拦截。
- **钦定可移植形态**：Python helper（两 OS runtime 都是 Python）。

---

## 6. 自动加载：两层池来源 + `bot_skills` 可见性过滤

### 6.1 新来源
- `backend/skills/sources/external.py` → `ExternalPoolSource(ctx)`：
  - 全局层：`scan_dir(external_global_skills_dir, "external_global")`。
  - 群组层（`ctx.group_id` 有值时）：`scan_dir(group_external_skills_dir(gid), "external_group")`。
  - `signature()`：两层 `dir_signature` 合并。
  - 枚举**全部**（不在此过滤；过滤见 6.3）。

### 6.2 接入扫描
- `discovery._sources()` 增加 `ExternalPoolSource`。
- `discovery._scan_signature()` 并入其 signature。
- `merge_layers` 加 `external_global` / `external_group` 列表；覆盖链：
  `system < group < role < external_global < external_group < learned.active < learned.personal`。
  `_LAYER_ORDER` 增两键（置 role 与 learned 间）。名字冲突仅产生诊断。

### 6.3 可见性过滤（缓存外，按 bot 查 `bot_skills`）
- **不能**把 `bot_skills`（DB）放进 mtime-签名缓存。
- 方案：缓存扫描照常返回**全部** external skill；在 **`available(bot)` 包装**（缓存之后）对每个 `layer ∈ {external_global, external_group}` 的条目，查 `bot_skills(bot_id, name).enabled`，未命中即剔除。
- 文件缓存仍有效；分配每次取最新；**可见性看 `bot_skills`，执行审批看 `permission_rules`**——两件事分离。

### 6.4 companion 文件列表加上限（借 OpenCode）
- `run_skill` 现列全部 companion（loader.py:138）；改采样**上限 N（如 10）**。

---

## 7. 分配：接口 + UI

### 7.1 接口（一等分配 + 执行审批分离）
- `GET /api/groups/{gid}/members/{bot_id}/skills`
  → `{ pool: [{name, description, version, platforms, high_privilege, source}...], assigned: [{name, enabled}...] }`。
- `PUT /api/groups/{gid}/members/{bot_id}/skills` body `{ assigned: [{name, enabled}] }` → 增删改 `bot_skills` 行（带 `assigned_by`）。
- 执行审批仍走 `permissions/routes.py`（可选地为高权技能配 ask/allow）。

### 7.2 UI（Bot 配置「技能」面板）
- 列两层池：description + 版本 + 可移植性徽章 + 高权限警告 + 来源（全局/本群）。
- 每个 skill：**分配开关**（写 `bot_skills`）+（高权技能）审批策略下拉（写 `permission_rules`）。
- 「导入 skill」按钮 → 选 scope（全局/本群，按权限）→ 填 git URL → 驱动 §4，刷新池。
- 管理入口：已导入技能列表（来源/版本/导入者）+ remove。

---

## 7.5 运行时交互与执行层增强（inline / fork / compaction）

参考 OpenCode/Claude Code 后定的执行层取舍。前提：只采纳其执行层精炼处，守住我们的多租户/隔离/权限衰减。

### 7.5.1 inline 正文框架 —— 保留 `tool` 结果（**不**学 user 消息）
- 现状保留：正文作 **`role:"tool"`** 进 `messages`（`tool_loop_v1_helpers.py:636`，按 `tool_call_id` 配对）。
- 不学 Claude Code 的「stub 结果 + 注入 `user/isMeta`」：① 破坏 tool-use 协议配对，多 provider 易踩校验；② 「user 更服从」无证据，模型本就把工具结果当权威；③ **群组场景** user 消息带 sender 身份，伪装正文会污染「谁说的」；④ run_skill 已在 `_MICROCOMPACT_TOOLS`，改 user 要重做。
- **唯一增强**：正文包 `<skill_instructions>…现在按以下步骤执行…</skill_instructions>`。

### 7.5.2 fork 升级为真子 agent（采纳 Claude Code + 我们的安全约束）
- 现状：fork = 单轮 `ai_service.call`，请求工具调用**不执行**（`tool_loop_v1_helpers.py:188`）——近乎无用。
- 升级：`context:fork` 路由到现成子 agent 设施（`spawn_agent`/AgentTool runner），对齐多轮+工具。**三条强制约束**：
  1. **走 `derive_subagent_ruleset()`**：bypass 不下传、blanket high-risk 被 drop（我们比 Claude Code 严谨，不退回）。
  2. **套 `spawn_depth` 上限**：防技能调技能指数爆炸。
  3. **工具门控**：声明 `allowed_tools` → 多轮子 agent；未声明 → 轻量单次调用。
- 回传父对话仍只是子 agent 的**结果文本**。

### 7.5.3 compaction 保活（采纳 `addInvokedSkill`）
- 问题：任务中途技能正文被压缩丢弃 → 模型断片。现状只有 micro-compact。
- 增强：已 `run_skill` 加载的技能正文登记「已调用」，压缩时 **pin 住或压缩后预算内还原**（含 base-dir 头 / `${SKILL_DIR}` 已替换版本）。
- 顺带：`skill_model` 处理 `[1m]` 窗口后缀。

---

## 8. 受影响文件清单（实现期参考）

| 文件 | 改动 | 计划 |
|---|---|---|
| `backend/db/schema*.py` | 新表 `bot_skills`、`external_skills` | A |
| `backend/skills/assignment.py`（新） | `bot_skills` CRUD + `available(bot)` 可见性过滤 | A |
| `backend/permissions/patterns.py` | `synthesize_args_pattern` 补 `run_skill`（name-scoped，修 blanket bug） | A |
| `backend/permissions/engine.py` | run_skill 规则只匹配 `name` 字段 | A |
| `backend/executors/plugins/tool_loop_v1*.py` | fork→子 agent + `derive_subagent_ruleset` + `spawn_depth`；compaction 保活；`[1m]` | A |
| `backend/skills/loader.py` | inline 包 `<skill_instructions>`；fork 工具门控；Windows `${SKILL_DIR}` 归一化；companion 上限 | A |
| `backend/workspace/layout.py` | `external_global_skills_dir` / `group_external_skills_dir` | B |
| `backend/skills/sources/external.py`（新） | `ExternalPoolSource`（两层） | B |
| `backend/skills/discovery.py` | `_sources()` / `_scan_signature()` 加外部池 | B |
| `backend/skills/composer.py` | `merge_layers` + `_LAYER_ORDER` 加两 external 键 | B |
| `backend/skills/importer.py`（新） | git 拉取+护栏、消毒、归一化、不可信收紧、分类、落盘+写注册表 | B |
| `backend/skills/registry.py`（新） | `external_skills` CRUD（import/remove，预留 update/pin/audit） | B |
| `backend/skills/metadata.py` | frontmatter 加 `shell`、`platforms`、`version` 解析 | B |
| `backend/api/skills.py` | `POST /import`、`DELETE /external/{id}`（预留 update/pin） | B |
| `backend/api/groups.py` | `GET/PUT .../members/{bot_id}/skills`（写 `bot_skills`） | B |
| frontend Bot 配置面板 | 分配开关 + 审批策略 + 导入/管理 UI | B |
| `backend/scripts/migrate_skill_assignment.py`（新） | blanket run_skill 规则 → 显式 `bot_skills` | C |

---

## 9. 测试要点（实现期）

- **能力≠权限分离**：`bot_skills.enabled=0` → 不可见；`enabled=1` 但 permission=ask → 可见、调用时审批；两者独立可测。
- **name-scoped 匹配**：`run_skill(name="build", args="deploy")` 不再误中 `deploy` 规则。
- **导入护栏**：`..`/绝对/盘符/逃逸 symlink 拒绝；超 size/时长拒绝；非白名单 host 拒绝。
- **注册表一致性**：import 写文件+行；remove 删两者；重名同 scope 被 `UNIQUE` 拒。
- **不可信收紧**：导入 skill 的 `!\`cmd\`` 不被执行。
- **两层可见性 + 隔离**：群组池仅本群 bot 可见；bot A 分配不影响 bot B；全局池定义跨群组可见。
- **覆盖顺序**：`external_group` 盖 `external_global` 盖 `role`，被 `learned` 盖过；signature 对池增删改敏感。
- **跨平台**：Windows `${SKILL_DIR}` 归一化；`shell:`/`{{ is_windows }}` 生效。
- **inline 框架**：正文走 `role:"tool"` + `<skill_instructions>`。
- **fork 子 agent**：声明 `allowed_tools` 的 fork 能多轮跑工具；经 `derive_subagent_ruleset` 衰减；超 `spawn_depth` 被拒；未声明工具仍单次。
- **compaction 保活**：已调用技能正文经压缩仍在；`[1m]` 后缀保留。
- **迁移**：旧 blanket run_skill 规则展开为各 bot 当时可用技能的显式 `bot_skills`，无 bot 因迁移瘫痪。

---

## 10. 迁移（一等动作，非静默）

- **建表**：`bot_skills`、`external_skills`（幂等 `CREATE TABLE IF NOT EXISTS`）。
- **权限收紧迁移**：`synthesize_args_pattern` blanket→name-scoped 是行为变更（旧「always allow 某技能」实为放行全部）。`scripts/migrate_skill_assignment.py`：按每个 bot 迁移时**实际可用**技能，把旧 blanket run_skill allow 规则**展开为显式 `bot_skills` 分配 + 必要的 name-scoped permission**。**不静默** + release note。
- **回滚**：迁移脚本保留 dry-run 与备份（对齐项目既有 `migrate_*` 约定）。

---

## 11. 实现计划拆分（地基先于特性）

- **Plan A（地基，所有技能受益）**：`bot_skills`/`external_skills` 建表 + 分配/可见性分离 + name-scoped 权限（修 blanket bug）+ §7.5 执行层（fork→子 agent、compaction 保活、inline 包装、`[1m]`）+ companion 上限 + `${SKILL_DIR}` 归一化。
- **Plan B（外部 skill 特性）**：两层 external 池 + `ExternalPoolSource` + merge 接入 + git 导入注册表 + 跨平台 frontmatter + 分配/导入 API + UI。
- **Plan C（迁移）**：`migrate_skill_assignment` + release note + 灰度。

> 顺序理由：A 是地基（fork/compaction/分配模型惠及全部技能，且风险高需先稳）；B 是用户可见特性，依赖 A 的分配模型；C 收尾兜底。

---

## 12. 实现期再定

- `external_skills.update`（re-pull 新 commit）的冲突/版本对比策略（v1 仅建模）。
- host 白名单默认集与配置位置。
- `shell` 默认 `bash`、`platforms` 默认 `pure`、`version` 缺省。
- fork 子 agent 的 `spawn_depth` 具体上限值。
- 可见性 `available(bot)` 包装的精确挂载点（建议单一函数，prompt-build 与 run_skill 共用）。
