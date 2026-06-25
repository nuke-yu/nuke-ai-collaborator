# 技能分层与角色绑定重做 — 设计 Spec

- 日期：2026-06-25
- 状态：待评审
- 作者：nuke（设计协作：AI）

## 背景与问题

系统设计了四层 skill 加载（System / Group / Role / Learned），但**角色层（L3）从设计上就接不上**，实测 16 个 bot 里只有 1 个真正拿到角色技能。根因有三：

1. **`bot.role` 是 UI 自由文本**（`MemberList.jsx` 的输入框、`TemplateManager.jsx` 的模板 role 字段都无约束），用户随意填出 `CEO` / `银行家` / `full stack engineer` / `Tester` 等值。
2. **L3 靠 `ROLES_ROOT/<bot.role>/skills` 精确字符串匹配**（`backend/skills/discovery.py`），自由文本几乎不可能撞中磁盘目录名 —— 唯一巧合命中的是 `需求分析师`，其余 bot 全部只拿到 L1 系统技能。
3. **三套命名各写各的、无单一真相源**：磁盘 `workspaces/roles/`（13 目录）、中央 DB `role_templates` 表（10 行、且**不带 skill**）、`members.role` 字段（自由文本）互不引用。

此外，`workspaces/roles/` 是**全局**目录、被所有群共用，**违反群隔离原则**（bot 属于唯一群，技能须按群隔离）。同名 `code-review`/`debug-error`/`write-unit-test` 在 5+ 个角色目录里各存一份副本，改公共逻辑要改多处。

## 设计目标

1. 让 L3 角色技能真正生效，且**按"群=项目"隔离**，群间互不影响。
2. `bot.role` 收敛为下拉选择（受约束），不再自由文本。
3. 角色与技能恒为**文件**（不进 DB），与开源 agent 通行做法一致；UI 作为文件管理器对其增删改。
4. 提供"逐层整理"的群内 UI，看清并维护本群四层技能。
5. 默认模板**中英文两套**，建群时按群语言拷对应语种。
6. 为将来"从 GitHub 导入技能（如 Claude skill）"预留扩展，但本 spec 不实现。

## 非目标（YAGNI / 留作后续 spec）

- **GitHub / 外部技能导入**：仅保证数据模型可扩展（技能为文件、格式接近 Claude skill 文件夹），实现独立成后续 spec。
- **角色级技能覆盖**（某角色对某技能写专属覆盖版）：当前无真实需求，暂不做。
- **生成技能（Learned/Personal，L4 反思沉淀）链路**：本次完全不动，仅在群内 UI 中展示与沿用既有 draft 审批。
- **角色重命名的级联维护**：以名字为键，重命名作为后续运维能力，不在本次范围。

## 总体模型

技能分两类来源，均落在文件系统：

- **策展技能（curated）**：人维护（将来可 GitHub 导入）。分布在 System 池、Group 层、Role 层。
- **生成技能（generated）**：bot 反思沉淀产出，留在 bot 本地（L4），本次不动。

### 分层与隔离

| 层 | 范围 | 运行时来源 |
|---|---|---|
| **System (L1) = 池** | **跨群共享**，通用技能 | `workspaces/system/skills/`（不变） |
| **Group (L2)** | **仅本群** | `workspaces/group_<id>/shared/skills/`（不变） |
| **Role (L3)** | **仅本群** | `workspaces/group_<id>/roles/<role>/skills/`（**新增；修掉全局目录违反隔离的 bug**） |
| **Learned/Personal (L4)** | 仅该 bot | `workspaces/group_<id>/bots/bot_<id>/skills/learned/…`（不变） |

只有 System 池是跨群共享的；Group / Role 全部钉死在群内。

### 文件布局

```
workspaces/
├── system/skills/                        # L1 池：跨群通用技能（UI 管理；将来 GitHub 导入落点）
├── templates/<lang>/roles/<role>/        # 全局角色模板，按语言分套（lang ∈ {zh, en}）
│   ├── role.yaml                          #   元数据：display_name / system_prompt / avatar_color
│   └── skills/*.md                        #   该角色默认技能
└── group_<id>/
    ├── shared/skills/                     # L2：本群群级技能（群内自治）
    ├── roles/<role>/                      # L3：本群角色（建群时从 templates 拷入，之后只属本群）
    │   ├── role.yaml
    │   └── skills/*.md
    └── bots/bot_<id>/skills/learned/…     # L4：bot 反思沉淀（不动）
```

`workspaces/templates/<lang>/roles/` 是**建群拷贝的模板源，运行时不被 discovery 扫描**。

## 数据模型

延续"角色和模板都是文件，不进 DB"。

### 1. `role_templates` 表退役 → 模板改为文件夹
- 模板真相源迁到 `workspaces/templates/<lang>/roles/<role>/`（`role.yaml` + `skills/`）。
- `TemplateManager` UI 改为 CRUD 模板文件夹（新 API，见下），不再读写 `role_templates` 表。
- 表保留为空壳一版以防回滚，下个迁移再 DROP。

### 2. 群的角色目录 = 文件系统派生
- 某群有哪些角色 = 列 `group_<id>/roles/*` 目录。**文件系统即注册表**，不建 per-group 角色表。

### 3. `bot.role` 收敛
- `members.role TEXT` 列**不改 schema**，但 `add_member`（`api/groups.py`）**校验** role 必须 ∈ 该群 `roles/` 目录，非法返回 422。
- UI 自由输入框 → 下拉，数据来自 `GET /api/groups/{id}/roles`。

### 4. 语义：snapshot vs live（有意区分）
- **system_prompt = 快照**：建 bot 时从 `role.yaml` 拷一份到 `members.system_prompt`，之后该 bot 独立可改；事后改群角色 prompt **不**回溯影响已建 bot。
- **技能 = 实时**：L3 运行时按 `group_<id>/roles/<bot.role>/skills/` 解析，改群角色技能则该群该角色所有 bot **立即生效**。
- 取舍理由：身份描述（prompt）跟人走、可个性化；能力（skill）跟角色走、统一升级。

### 5. `role.yaml` 结构
```yaml
display_name: 系统架构师
avatar_color: "#6366f1"
system_prompt: |
  你是本项目的系统架构师……
```
discovery **不读** `role.yaml`（它只列 skills），元数据仅由 roles API 读取。

## 默认角色模板目录（中英文各一套，共 12 角色）

在现有中文模板之上**新增** `Architecture` / `PM`。两套（zh / en）角色集一致：

| 角色（zh display） | 角色（en display） | 默认技能 |
|---|---|---|
| 代码助手 | Code Assistant | code-review, debug-error, write-unit-test |
| 后端Python专家 | Backend Python Expert | code-review, debug-error, design-api, write-unit-test |
| 后端Java工程师 | Backend Java Engineer | code-review, debug-error, design-api, write-unit-test |
| 前端工程师 | Frontend Engineer | code-review, debug-error, write-component |
| 系统架构师 | System Architect | design-architecture, tech-stack-review |
| 需求分析师 | Requirements Analyst | analyze-requirements, write-spec, write-user-story |
| QA测试工程师 | QA Engineer | bug-report, write-test-cases, write-test-plan |
| DevOps工程师 | DevOps Engineer | debug-deployment, write-ci-config, write-dockerfile |
| 写作助手 | Writing Assistant | polish-text, write-draft |
| 翻译专家 | Translation Expert | polish-translation, translate-text |
| **Architecture（新）** | **Architecture** | design-architecture, tech-stack-review |
| **PM（新）** | **PM** | write-spec, update-board, write-user-story |

> 注：`Architecture` 与 `系统架构师`、`PM` 与 `需求分析师` 在能力上有重叠，是按用户要求保留的并存默认角色，由 admin 后续按需精简。

**英文技能内容需新撰写**：现有 `*.md` 技能正文为中文，英文模板套需要对应的英文 SKILL 正文（可借 `翻译专家` 角色/人工产出）。此为一块独立工作量，列入实现计划。

被丢弃：`workspaces/roles/` 下的 `developer` / `qa` / `pm` 三个目录（更早一代英文残留、与中文角色重复，且 `role_templates` 无对应行）。

## 流程

### 建群拷贝（后端，UI 无感）
`create_group` 后新增一步：按 `get_group_language(group_id)`（缺省回退 zh）将 `templates/<lang>/roles/*` **整体拷贝**进 `group_<id>/roles/*`（role.yaml + skills 一起）。
- 默认全量拷贝作起点，群里再删用不上的角色（不在建群时勾选，YAGNI）。
- System 池（L1）不拷贝，是跨群共享引用。
- 挂在现有 `ensure_group_db_ready` 那条群初始化路径附近，保证**幂等**（已存在不覆盖）。

### UI 链路 ①：全局管理台（admin 范围，跨群）
- **System 池**：CRUD `workspaces/system/skills/` 通用技能。
- **角色模板**：`TemplateManager` 升级，CRUD `templates/<lang>/roles/*`，编辑元数据 + 维护 `skills/`，按语言切换。
- 新 API：`/api/library/skills`（池 CRUD）、`/api/templates/roles`（模板 CRUD，替代旧 `/api/templates`）。

### UI 链路 ②：群内技能整理台（"逐层整理"主界面，扩展 `SkillPanel.jsx`）
- **System (L1)**：只读，标"继承自全局池"。
- **Group (L2)**：CRUD 本群群级技能。
- **Role (L3)**：列本群角色，进每个角色 CRUD 其技能、编辑 role.yaml。
- **Learned/Personal (L4)**：按 bot 展示反思沉淀（draft 待审批 / active），沿用既有 draft 审批。
- 新 API：`/api/groups/{id}/roles`（列/CRUD 角色）、`/api/groups/{id}/roles/{role}/skills`、`/api/groups/{id}/shared/skills`（L2 CRUD）。

### UI 链路 ③：建 bot 选角色（`MemberList.jsx`）
- 自由输入框 → 下拉，数据来自 `GET /api/groups/{id}/roles`。
- 选中后带出该角色默认 system_prompt（快照，可改）。`add_member` 后端校验 role ∈ 群角色。

## `discovery.py` 改动点（外科手术式，四层合并/缓存/注入逻辑不动）

1. **L3 路径切群内**（`_compute_skills_all`，约 discovery.py:281-283）：
   `_scan_dir_sync(ROLES_ROOT / role / "skills", "role")`
   → `_scan_dir_sync(layout.group_roles_dir(group_id) / role / "skills", "role")`；`group_id is None` 时跳过 L3。
2. **签名同步改**（`_scan_signature`，约 discovery.py:43-48）：L3 目录换为同一群内路径，保证缓存失效追踪正确文件。
3. **`constants.py` / `layout.py`**：
   - `ROLES_ROOT` 不再被 discovery 引用；改指 `TEMPLATES_ROOT = WORKSPACE_ROOT/"templates"`（仅迁移与建群拷贝用）。
   - `layout.py` 加 `group_roles_dir(gid)` → `group_dir(gid)/"roles"`、`templates_roles_dir(lang)` → `_root()/"templates"/lang/"roles"`。
4. L1 池 / L2 / L4 路径不动。

## 迁移

脚本 `backend/scripts/migrate_role_skills.py`，沿用 `migrate_workspace_layout` 规矩：**默认 dry-run，`--apply` 生效；先停服务、备份 `workspaces/` + 中央 DB**。

**A. 建全局模板** `workspaces/templates/<lang>/roles/<role>/`
- 老 `workspaces/roles/<role>/skills/*.md` → 拷进 `zh` 模板套的 `skills/`。
- `role.yaml` ← 合并：若 `role_templates` 有同名行取其 system_prompt/avatar/name，否则合成最小 role.yaml。
- 新增 `Architecture` / `PM` 两角色（技能见上表）。
- 丢弃 `developer` / `qa` / `pm` 残留目录。
- `en` 模板套：role.yaml 元数据可机翻/对照填，**英文技能正文作为独立任务产出**（迁移脚本先建骨架 + 占位，正文后续补）。

**B. 给现有 12 个群灌角色**
- 每个 `group_<id>/`：按群语言拷 `templates/<lang>/roles/*` → `group_<id>/roles/*`（幂等，已存在跳过）。

**C. 对齐现有 16 个 bot 的自由文本 role**
- 命中群角色（`需求分析师`）→ 不动。
- 不命中（`CEO`/`银行家`/`full stack engineer`/`Tester`…）→ **在该群自动建同名空角色**（空 skills + 合成 role.yaml）。bot 保留身份、行为同今天（只拿 L1）、role 满足下拉约束；admin 事后可补技能。**非破坏性**。

**D. 退役老全局目录**
- 旧 `workspaces/roles/` 改名 `workspaces/roles.legacy/`（运行时已不扫），留一发布周期回滚兜底，下个迁移再删。

## 测试

- `backend/tests/test_skills_group_path.py`：扩展覆盖 "L3 解析到 `group_<id>/roles/`、跨群不串"。
- 新增 `add_member` role 校验测试（合法下拉值通过、非法 422）。
- 建群拷贝测试：新群按语言拿到对应模板套、幂等、System 池不拷。
- 迁移脚本测试：dry-run 无副作用；apply 后老群有角色、不命中 bot 自动建空角色、`roles.legacy` 改名到位。
- roles / library / shared-skills 新 API 的 CRUD 单元测试。

## 待评审决策（已确认）

- [x] 技能恒为文件、不进 DB。
- [x] GitHub 导入留作后续 spec，仅保证可扩展。
- [x] System=跨群池；Group/Role 群内隔离；全局模板建群时拷贝、之后群内自治。
- [x] 默认目录 = 现有 10 中文角色 + Architecture + PM；丢弃 developer/qa/pm。
- [x] 中英文两套模板，建群按群语言拷。
- [ ] `Architecture` / `PM` 默认技能组合（本 spec 暂定值，待 review 确认）。

## 影响与风险

- **群隔离**：L3 从全局改群内，正向修复隔离违例，符合 `group-isolation-inviolable` 原则。
- **签名缓存**：L3 路径变更须同步 `_scan_signature`，否则缓存追踪错文件、改动不生效（已列为改动点 2）。
- **部署纪律**：迁移须停服务 + 备份后 `--apply`，与既有 `migrate_workspace_layout` 同等对待。
- **英文技能正文撰写**：体量不小，作为实现计划中的独立任务，可并行或分批。
