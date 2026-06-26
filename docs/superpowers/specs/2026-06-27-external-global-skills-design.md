# 外部 / 全局 Skill：识别、自动加载、分配给 Bot

**日期**：2026-06-27（rev2：分配改用 permission_rules，跨平台改用「作者声明」，对齐 OpenCode / Claude Code 实现）
**分支**：`design/skill-layer-role-binding`
**状态**：设计已确认，待写实现计划

---

## 0. 参考实现（已读源码）

- **OpenCode**（`/Users/Nuke/opencode/packages/opencode/src/{skill,tool/skill.ts,config/skills.ts}`）：扁平池 + 远程 `index.json` 拉取 + `available(agent)` 用权限过滤可见性。
- **Claude Code**（`/Users/Nuke/claude-code-haha-main/src/{tools/SkillTool,skills/loadSkillsDir.ts,utils/frontmatterParser.ts}`）：skill=slash-command；inline/fork；`${CLAUDE_SKILL_DIR}` + base-dir 头 + Windows 反斜杠归一化；**权限规则 `Skill(name)`/`Skill(name:*)` + safe-properties 自动放行**；**`shell: bash|powershell` frontmatter——作者声明可移植性，不按主机门禁**；不可信来源（MCP/远程）禁内联 shell。

两套**都用权限规则**决定「这个 agent 能看到/调哪些 skill」，跨平台**都靠作者声明 + 路径归一化**而非按主机 OS 分类门禁。本设计据此对齐。

### 0.1 三方对比与取舍

| 维度 | 我们（现状） | OpenCode | Claude Code | 我的建议 / 评价 |
|---|---|---|---|---|
| 执行层 tool | `run_skill`：base-dir 头 + `${SKILL_DIR}` + 参数替换 + companion 列表 + fork | 注入正文 + base dir + 文件列表（不替换） | inline/fork + `${CLAUDE_SKILL_DIR}` + 内联 shell | **保留我们的**——已是三者最全的执行层。仅补两处：Windows `${SKILL_DIR}` 反斜杠归一化、companion 文件列表加采样上限。 |
| inline / fork | ✅ | ❌（只 inline） | ✅ | **保留，无需改**。我们与 Claude Code 一致，OpenCode 缺 fork。 |
| 跨平台 | `template:true` Jinja 暴露 `{{ os/is_windows/shell }}` | 无 OS 感知 | `shell:` frontmatter 作者声明 + 路径归一化 | **采纳 Claude Code 的「作者声明」**：加 `shell: bash\|powershell` frontmatter，复用我们已有的 Jinja，废掉「按主机 OS 硬门禁」；`platforms` 降为 UI 徽章/警告。 |
| 分配 / 可见性 | 原计划：`_assigned.json` 清单 + 新 source | 权限规则 `Permission.evaluate("skill",…)` | 权限规则 `Skill(name)`/`(name:*)` + safe-props 自动放行 | **采纳权限规则**（两家共识）：复用现有 `permission_rules`，弃清单方案。可见性==执行门禁同源，零新文件状态。 |
| 分层 merge | ✅ 4 层 + A1 保护 + 深合并 + 诊断 | 扁平、后者覆盖 | 扁平、按目录深度 | **保留我们的，是优势**——多群组/角色/bot 场景必需，别学扁平。外部池作第 5 源插在 `role` 与 `learned` 之间。 |
| 导入安全 | `HIGH_PRIVILEGE` 诊断 | —（来源视为可信） | 不可信来源（MCP/远程）禁内联 shell | **两者叠加**：HIGH_PRIVILEGE 诊断 + 不可信导入禁内联 shell + 未知 frontmatter 属性 fail-safe 要审批。 |
| 远程导入 | 无 | `index.json` 清单 + 逐文件下载 + 缓存 | `_canonical_` 远程拉取 + GCS/AKI 缓存（实验） | **本期用 git URL**（用户已定）；`index.json` 式 registry 留作后续「skill 市场」演进参考。 |

> 一句话取舍：**执行层、inline/fork、分层 merge 我们已领先 → 保留**；**分配、跨平台、导入安全 → 采纳两家的成熟做法**。

---

## 1. 目标

让 Bot 能使用**外部 skill**。三项能力：

1. **识别**——把外部 skill 从 git URL 导入系统，进入**全局外部池**。
2. **自动加载**——被分配的全局 skill 自动进入 Bot 的 prompt / tool loop，与现有各层 skill 同等流转。
3. **分配**——在 UI 上把全局 skill 分配给指定 Bot（=写一条 `permission_rules`）。

| 能力 | 实现 |
|---|---|
| 识别 | `POST /api/skills/import`，git clone → 校验 → 落 `workspaces/external/skills/` |
| 自动加载 | 新 `ExternalPoolSource` 接入扫描；可见性由**权限引擎**按 bot 过滤 |
| 分配 | `permission_rules(tool_pattern="run_skill", args_pattern="<name>", action)` per bot |

---

## 2. 前置事实与边界

- **Skill 在服务端执行**：backend 主机的 per-group 沙箱（生产 Linux，dev Mac），过 `run_shell` guard。Bot 不在用户机器上跑。
- **`run_skill` 执行层已就绪且比参考实现更强**：`loader.py:run_skill` 已注入 base-dir 头（:133）+ `${SKILL_DIR}` 替换 + 参数替换 + companion 文件列表（:137）+ inline/fork（:155）。**本设计不重写执行层**，只补外部池来源、可见性过滤、导入。
- **`${SKILL_DIR}` 已实现**（loader.py + processor）——不是未决项。
- **依赖项（不解决）**：现有沙箱（`NUKE_SHELL_EXEC_BACKEND=container`，bwrap/Docker）仅 Linux。Windows backend 的 shell-exec 沙箱是已存在缺口，本工作依赖它但不解决它。
- **群组隔离不破**：外部池是 skill **定义**（指令文本），跨群组可发现；**分配 per-bot**。bots / memory / 对话历史仍隔离。

---

## 3. 数据模型

### 3.1 全局外部池 = 新建 opt-in 目录（**不是** system 层）
- 物理位置：**`workspaces/external/skills/<name>/`**（新建）。
- 为何不用 `system` 层：system 层是 always-on + A1 保护，会被**强制注入给所有 bot**且不可遮蔽——与「per-bot 显式分配」相悖。外部池必须**默认 opt-in**：bot 只有在有 allow 规则时才看得到/调得到。
- 现有 `system / group / role / learned` 四层**行为不变**。

### 3.2 Skill 单元 = 文件夹（现状，无需改）
- `<name>/SKILL.md`（frontmatter 为索引）+ 附带 `scripts/`、`helper.py`、`references/`。
- `scan_dir` 已识别目录 skill；`SkillStore.copy` 用 `copytree` 整体搬运 + symlink 逃逸检查。

### 3.3 分配 = `permission_rules`（复用现有权限层）
- 现有表：`permission_rules(id, bot_id, tool_pattern, args_pattern, action)`，中央 DB 按 `bot_id`。
- 引擎 `permissions.engine._matches`：`tool_pattern` fnmatch 工具名；`args_pattern` fnmatch 任一参数值（递归）。
- **分配一个 skill**：`(tool_pattern="run_skill", args_pattern="<skill_name>", action="allow")`。
- **分配一族**：`args_pattern="<prefix>*"`。**拒绝**：`action="deny"`。
- **可见性 == 权限**（镜像 OpenCode `available(agent)`）：bot 的「可用外部 skill」= 对合成调用 `run_skill(name=<skill>)` 评估为非 deny 且命中 allow 的那些。同一引擎既管**可见性**（注入哪些到 system prompt）又管**执行**（`run_skill` 的 `_permission_check_hook` 已在用），天然一致。
- **`synthesize_args_pattern` 补 `run_skill` 分支**：返回 `_escape_glob(name)`，让「always allow 此 skill」生成 name-scoped 规则而非 blanket（现状 run_skill 落 blanket，会放行全部 skill——是 bug，一并修）。
- **safe-properties fail-safe**（借 Claude Code）：导入 skill 含未知 frontmatter 高权属性时，可见但执行仍需审批；未知新属性默认要审批。

---

## 4. 识别：导入与校验

**接口**：`POST /api/skills/import`（token 鉴权，DFT-082），body `{ "git_url": "...", "ref": "main" }`。

**流水线**（全程临时目录，校验通过前不碰外部池）：

1. **拉取**——`git clone --depth 1 --branch <ref> <git_url>` 到 temp。
2. **条目消毒**——拒绝逃逸根目录路径（`..`、绝对路径、盘符），归一化 `\`→`/`，拒绝逃逸 symlink（复用 `store.copy` 检查）。
3. **形态检查**——必须有 `SKILL.md`；`parse_skill_meta` 解析；`name`/`description` 缺失或 `name` 不过 `_is_safe_name` → 拒绝。仓库内多个 skill 文件夹逐个导入。
4. **归一化**——去 BOM；`.sh` 的 CRLF→LF（Linux shebang）；保留 `.ps1` 的 CRLF。
5. **不可信安全收紧**（借 Claude Code）——
   - 导入 skill 视为**不可信来源**：默认**禁内联 shell 注入**（`!\`cmd\`` / ```! ``` 块不在导入 skill 上执行），镜像现有 MCP-skill 规则。
   - 扫 `HIGH_PRIVILEGE_TOOLS`（复用 `store.write` 子串扫描）→ 标 `high_privilege`，UI 警告。
6. **可移植性分类**（见 §5）→ 写入 `platforms`（仅 UI 提示用）。
7. **落盘**——原子移动进 `workspaces/external/skills/<name>/`；mtime 变化使缓存失效，`SkillWatcher` 自动捡起。
8. **重名处理**——拒绝或加版本后缀（实现期定），回传 UI。

**返回**：`{ imported: [{name, platforms, high_privilege}], rejected: [{path, reason}] }`。

---

## 5. 可移植性策略（Linux + Windows backend）—— 作者声明，不按主机门禁

对齐 Claude Code：**skill 作者声明可移植意图，渲染侧做路径归一化**；不按主机 OS 分类丢弃 skill。

- **`shell: bash | powershell` frontmatter**（借 Claude Code）：声明内联 `!`cmd`` 块用哪个 shell；文件级；不读 host 的 defaultShell——「作者挑 shell，不是读者」。
- **复用现有 `template:true` Jinja**（loader.py:121-130 已有）：暴露 `{{ os }}/{{ is_windows }}/{{ shell }}`，作者可在正文按 OS 分支。
- **`${SKILL_DIR}` Windows 反斜杠归一化**（借 Claude Code loadSkillsDir.ts:361）：Windows 下把注入的 base dir `\`→`/`，避免内联命令把反斜杠当转义、相对路径解析错。**这是 run_skill 需补的一小处**。
- **`platforms` 分类仅作 UI 徽章**（`pure/posix/windows/cross`）——**不做硬门禁**（无法改写第三方正文，硬丢弃反而割裂体验）。分配 `posix`-only skill 到 Windows 主机时 UI 给**警告**，不拦截。
- **钦定可移植形态**：Python helper（两 OS runtime 都是 Python）；导入文档与 UI 引导优先。

---

## 6. 自动加载：外部池来源 + 权限可见性过滤

### 6.1 新来源
- 新建 `backend/skills/sources/external.py` → `ExternalPoolSource(ctx)`：
  - `enumerate()`：`scan_dir(workspaces/external/skills, "external")`。
  - `signature()`：`dir_signature(外部池)`。
  - 枚举**全部**外部 skill（不在此过滤；过滤在缓存外做，见 6.3）。

### 6.2 接入扫描
- `discovery._sources()` 增加 `ExternalPoolSource`。
- `discovery._scan_signature()` 并入其 signature。
- `merge_layers` 签名加 `external` 列表，插在 `role` 与 `learned` 之间：
  `system < group < role < external < learned.active < learned.personal`。
  `_LAYER_ORDER` 增 `"external"`（置 role 与 learned 间，重排数值）。

### 6.3 可见性过滤（缓存外，按 bot 用权限引擎）
- **不能**把权限放进 `list_skills_all` 的 mtime-签名缓存（规则在 DB、非文件系统，签名抓不到变更）。
- 方案：缓存的四/五层扫描照常返回**全部** external skill；在 **prompt-build / available 步骤**（缓存之后）对每个 `layer=="external"` 的条目，用 `permissions.engine` 评估合成调用 `run_skill(name=<skill>)`——非 allow 即从可注入列表剔除。
- 这样：文件系统缓存仍有效；权限规则每次取最新；可见性与执行门禁同源。

### 6.4 companion 文件列表加上限（借 OpenCode）
- `run_skill` 现列**全部** companion（loader.py:138）；改为采样**上限 N（如 10）**，避免大 skill 撑爆上下文。

---

## 7. 分配：接口 + UI

### 7.1 接口（复用现有权限路由 / group-scoped）
- 优先复用 `permissions/routes.py` 的规则增删（写 `run_skill` + skill name 的规则）。
- 便捷只读端点（可选）：`GET /api/groups/{gid}/members/{bot_id}/skills`
  → `{ pool: [{name, description, platforms, high_privilege}...], assigned: [name...（由规则推导）] }`。
- 分配/取消 = 增删 `permission_rules` 行（`tool_pattern="run_skill"`, `args_pattern=name|prefix*`, `action`）。

### 7.2 UI（Bot 配置「技能」面板）
- 列全局外部池（扫 `workspaces/external/skills`）：description + 可移植性徽章 + 高权限警告。
- 每个 skill 一个 allow/deny 开关 → 增删对应 `permission_rules`。
- 「导入 skill」按钮 → 填 git URL → 驱动 §4，完成刷新池。

---

## 8. 受影响文件清单（实现期参考）

| 文件 | 改动 |
|---|---|
| `backend/skills/sources/external.py`（新） | `ExternalPoolSource` |
| `backend/skills/discovery.py` | `_sources()` / `_scan_signature()` 加外部池来源 |
| `backend/skills/composer.py` | `merge_layers` 签名 + `_LAYER_ORDER` 加 `external` |
| `backend/skills/importer.py`（新） | git 拉取、消毒、归一化、不可信收紧、分类、落盘 |
| `backend/skills/loader.py` | `run_skill`：Windows `${SKILL_DIR}` 归一化、companion 上限、导入 skill 禁内联 shell |
| `backend/skills/metadata.py` | frontmatter 加 `shell`、`platforms` 解析 |
| `backend/permissions/patterns.py` | `synthesize_args_pattern` 补 `run_skill` 分支（name-scoped，修 blanket bug） |
| prompt-build / available 路径 | 对 `layer=="external"` 按 bot 做权限可见性过滤 |
| `backend/api/skills.py` | `POST /api/skills/import` |
| `backend/api/groups.py`（或 permissions 路由） | bot↔skill 分配端点 |
| frontend Bot 配置面板 | 技能分配（allow/deny）+ 导入 UI |

---

## 9. 测试要点（实现期）

- **导入消毒**：`..` / 绝对路径 / 盘符 / 逃逸 symlink 全部拒绝。
- **不可信收紧**：导入 skill 正文里的 `!\`cmd\`` 不被执行。
- **权限可见性**：无规则 → 外部 skill 不出现在 bot 可用列表也不可 `run_skill`；加 allow 规则 → 立即可见可调；deny 覆盖 allow。
- **`synthesize_args_pattern`**：run_skill「always allow」生成 name-scoped 规则，不再 blanket 放行全部 skill。
- **覆盖顺序**：`external` 盖过 `role`，被 `learned` 盖过；signature 缓存对外部池增删改敏感。
- **跨平台**：Windows 下 `${SKILL_DIR}` 反斜杠归一化；`shell:`/`{{ is_windows }}` 分支生效。
- **群组隔离**：bot A 的分配规则不影响 bot B；外部池定义跨群组可见。

---

## 10. 实现期再定

- 重名导入策略：拒绝 vs 版本后缀。
- 可见性过滤挂载点：prompt-build vs 一个 `available(bot)` 包装函数（建议后者，单一真相）。
- `shell` / `platforms` frontmatter schema 细节与默认值。
- 便捷只读分配端点是否需要，还是纯走 permissions 路由。
