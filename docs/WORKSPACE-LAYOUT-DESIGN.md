# 工作区目录布局设计（Workspace Layout）

> 最后更新：2026-06-09
> 状态：设计定稿（实现指导与标准）
> 关联文档：[PROJECT-CELL-ISOLATION-V3.md](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/docs/PROJECT-CELL-ISOLATION-V3.md)、[BOT-COLLABORATION-DESIGN.md](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/docs/BOT-COLLABORATION-DESIGN.md)、[superpowers/specs/2026-06-07-multi-project-ai-team-foundation-redesign.md](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/docs/superpowers/specs/2026-06-07-multi-project-ai-team-foundation-redesign.md)

本文定义 `backend/workspaces/` 的目录布局、各区语义、持久/易逝分级与保留策略，作为后续实现的**指导与标准**。

---

## 一、背景与动机

群组式 AI 协作平台里，一个群组有 1–2 个真人 + 若干角色 Bot（BA / Dev / QA / PM …），按 `BA→Dev→QA` 协作链推进研发。当前实盘暴露两个结构问题：

1. **协作链在"交接"处断裂**：Dev 写的代码落在自己私有的 `bot_{id}/`，QA 沙箱在另一个空的 `bot_{id}/`，**QA 读不到 Dev 的产出**（曾用 MCP filesystem 指向 `backend/workspace/` 当临时共享盘，越权又冲突，已废弃）。
2. **bot 层级摆错**：bot 当前扁平挂在 `workspaces/bot_{id}`，与群组平级；但 **bot 归属于群组、跨群组不共享**，层级应收归群组之下。

目标：用一棵清晰的目录树，落实「**群组为隔离单元**」「**bot 私有 vs 群组共享**」「**持久知识 vs 易逝日志**」三条边界，并打通 Dev→QA 的代码交接。

---

## 二、核心原则

1. **群组是唯一隔离单元**：一个群组的全部（bot 私有区、共享区、聊天、日志）都在 `group_{id}/` 下。删一个群组 = 一刀切，跨群组零泄漏。与 CLAUDE.md「群组之间完全隔离：独立 Bot 员工、独立对话、独立知识库」对齐。
2. **bot 私有 ≠ 群组共享**：bot 的身份/性格/能力/记忆/私有草稿属私有区，bot 之间互不可见；要协作交接的产物（代码、文档）才进共享区。
3. **代码 = git 工作树，交付 = git merge**：代码是从 GitHub clone 下来的 git 工作树（一个项目可含多个微服务 repo）。Dev/QA 在**同一 checkout、同一 branch** 上协作；全绿后 `push → GitHub merge` 即交付。**不靠目录搬运区分"在制品/成品"——git 的 branch+merge 已是这条边界。**
4. **持久知识 vs 易逝日志分级**：只有 4 样长期保留（代码 / 共享文档 / 记忆 / 对话）；其余（run 执行痕迹、私有草稿）按保留策略回收，不当文档永久堆积。
5. **不变量：bot 必属一群**：每个 bot 恰好属于一个群组（`members.group_id` 对 bot 永远非空），bot 目录 ⟺ DB 有该 bot ⟺ 它有 group。不存在"无 group 的 bot"。`member_id=0 / group_id=0` 仅是系统消息的虚拟占位，**不是有工作区的 bot**。

---

## 三、完整目录树

标注：`[既有]` 现状保留 · `[变更]` 结构调整 · `[新增]` · `[待定]` 见 §七。

```
workspaces/
│
├── system/                         [既有] 系统级（非群组），全局共享
│   ├── skills/                     全局技能库
│   └── traits/                     全局特质库
│
├── roles/                          [既有] 系统级，角色模板（BA/Dev/QA/PM…）
│
└── group_{id}/                     ★ 隔离边界：一个群组的全部都在这；跨群组零共享
    │
    ├── chat.db / -wal / -shm       [既有] 群聊状态（基础设施，bot 不可见）
    ├── group.lock                  [既有] 群组锁
    ├── runs/                       [既有] 单次 run 的执行痕迹（易逝日志，见 §六）
    │   ├── 2026-06-08_HHMMSS_<id>.md
    │   └── archive/                [新增] 归档（压缩旧 run，可选）
    │
    ├── bots/                       [变更] 群组内 bot 的【私有】区（bot 间互不可见）
    │   │                                  原为顶层 workspaces/bot_{id}，现收归群组下
    │   ├── bot_{id}/               一个 bot 专属：身份 / 性格 / 能力 / 记忆 / 私有草稿
    │   │   ├── IDENTITY.md
    │   │   ├── SOUL.md
    │   │   ├── BOOTSTRAP.md
    │   │   ├── AGENT.md
    │   │   ├── MEMORY.md           私有记忆（持久，受写保护）
    │   │   ├── skills/             私有技能（learned/active, learned/draft, 手动）
    │   │   ├── logs/               私有日志
    │   │   └── .history/           私有文件版本历史（已有 _HISTORY_LIMIT 滚动）
    │   └── bot_{id2}/  …
    │
    └── shared/                     群组内【共享】区（仅本群组可见；= group_workspace(id)）
        ├── BOARD.md                [既有] 固定协调件：任务看板
        ├── SPEC.md                 [既有] 固定协调件：规格
        ├── API_CONTRACT.md         [既有] 固定协调件：接口契约
        ├── RETRO_LATEST.md         [既有] 固定协调件：复盘
        ├── prs/                    [既有] PR 记录
        ├── skills/                 [既有] 群组共享技能
        ├── docs/                   [新增] 群组共享文档（BA分析/QA报告/设计说明，自由产出，人管理）
        └── workspace/              [新增] 代码 git 树落点（只放代码，保持干净）
            ├── repo1/              微服务 1：.git + 仓库自身结构
            ├── repo2/              微服务 2：…
            └── …                   一个项目可含多个 repo
```

---

## 四、bot 私有区（`group_{id}/bots/bot_{id}/`）

- 存放 bot 的**身份/性格/能力/记忆**（`IDENTITY/SOUL/BOOTSTRAP/AGENT/MEMORY.md`）、私有技能、私有日志与版本历史。
- **bot 之间互不可见**；**跨群组绝不共享**（同一角色在不同群组是不同 bot，各自积累领域知识）。
- bot 的**临时草稿/笔记/思考**也留在这里——只是它自己的工作记忆，别人不需要看，随私有区生灭。
- `bots/` 仅是归类用的**普通父目录**，与 Docker/运行时隔离无关（进程/沙箱隔离由 supervisor+worker 与 run_shell 沙箱负责，与目录摆放无关）。

---

## 五、群组共享区（`group_{id}/shared/`）

仅本群组可见。三类内容刻意分开：

### 5.1 固定协调件（既有）
`BOARD.md / SPEC.md / API_CONTRACT.md / RETRO_LATEST.md`——结构化、命名固定的协作契约（看板、规格、契约、复盘）。类比"表格栏位"。

### 5.2 共享文档 `docs/`（新增）
bot **自由产出**的不定名文档：BA 的需求拆解、Dev 的设计说明、QA 的测试报告等。类比"附件夹"。
- 与 `workspace/`（代码）**刻意分开**：跨 repo1/2/3/4 的文档不属于任何单个 repo，不该被 commit 进某个微服务仓库污染其历史。
- 保留靠**人/bot 主动管理**（草稿该删就删），不机械按时间滚动——文档价值在内容。

### 5.3 代码 `workspace/`（新增）
群组共享的 **git 工作树**落点，平行容纳**多个 repo**（微服务）。
- 每个 `workspace/<repo>/` 是一棵独立 git 树（`.git` + 仓库原结构），内部**不强加任何分类目录**，由仓库自身决定。
- Dev/QA **共用同一 checkout、同一 branch**：Dev 写、QA 读测同一份。
- 交付：测试全绿 → `push branch → GitHub merge`。

---

## 六、持久 vs 易逝 + 保留策略

**持久（长期保留，是项目价值所在）——仅 4 样：**

| 类别 | 位置 | 说明 |
| :-- | :-- | :-- |
| 代码 | `shared/workspace/<repo>/`（git） | 版本由 git/GitHub 负责，永久 |
| 共享文档 | `shared/docs/` | 人/bot 主动管理保留 |
| 记忆 | `bots/bot_{id}/MEMORY.md` | 经提炼、体量小、受写保护 |
| 对话 | `group_{id}/chat.db` | 群聊记录（DB，可归档） |

**易逝（按策略回收，不当文档堆积）：**

| 类别 | 位置 | 回收方式 |
| :-- | :-- | :-- |
| run 执行痕迹 | `group_{id}/runs/*.md` | **数量 + 时长双封顶**，超出删除或归档（见下） |
| bot 私有文件历史 | `bots/bot_{id}/.history/` | 已有 `_HISTORY_LIMIT` 滚动删旧版本 |
| bot 私有草稿 | `bots/bot_{id}/` | 随私有区生灭，bot 自管 |

### 6.1 `runs/` 重新定性与保留策略

run 记录是**一次执行的痕迹/取证**（调了哪些工具、中间推理、子 agent 对话），**不是文档**：它真正有价值的产物早已被抽走（代码进 git、结论进 `docs/` 或 `MEMORY.md`、对话进 `chat.db`），剩下的原始 trace 久了即垃圾。几年期项目若把每次 run 当文档永久平铺，会无界增长。

**策略（默认值，可按真实磁盘占用调整）：**
- 每群组保留**最近 200 条** run，且**不超过 30 天**；
- 超出者：压缩归档进 `runs/archive/<period>.tar.gz`（如按季度），或直接删除（按合规要求二选一，默认归档）；
- 参照实现：`.history/` 已用 `_HISTORY_LIMIT` 做滚动删旧，runs/ 套同样思路加 age 维度。

> 同一原则适用 `shared/docs/` 的草稿，但那里靠人主动清理，不机械按时间砍。

---

## 七、决策记录（已定稿）

| # | 决策点 | 结论 |
| :-- | :--- | :--- |
| D1 | `runs/` 保留上限 | **最近 200 条 + 30 天**，超出归档。后续可按真实磁盘占用调整。 |
| D2 | 旧 run 处理方式 | **归档到 `runs/archive/`**（按周期打包），保留可追溯。 |
| D3 | `deliverables/` 去留 | **删除**——交付走 GitHub merge、不本地拷成品；其原有共享前缀重定向交给 `workspace/` + `docs/`（见 §八.2）。 |
| D4 | git 凭证 | **系统层已统一配置**（Win/Linux 一致），直接取用，不按群组单配（见 §八.4）。 |
| D5 | Dev↔QA 并发 | **无并发约束**——角色顺序执行，同一时刻仅一个 bot 操作工作树（见 §八.5）。 |

---

## 八、实现指导（正路设计 · 无临时方案）

> 这是未来整个协作的核心基础组件，按正路落地：**group 一等公民、显式贯穿；路径只有一个真相源；DB 反查只在边界发生一次。** 严禁把 DB 反查埋进叶子路径函数 + 缓存补救那种 hack。

### 8.0 三根支柱

**① 单一布局真相源（`workspace/layout.py`，新建）**
所有路径由一处纯函数计算，无 I/O，只吃显式 id：
```
group_dir(gid)            → workspaces/group_{gid}
bot_dir(gid, bot_id)      → group_{gid}/bots/bot_{bot_id}
group_shared_dir(gid)     → group_{gid}/shared
group_runs_dir(gid)       → group_{gid}/runs
```
- 现状问题：bot 路径有**两处重复定义**——`workspace.bot_workspace(bot_id)` 与 `skills.constants.bot_ws(bot_id)`，必须始终一致，否则技能读不到必崩。
- 改：二者**统一委托给 `layout.bot_dir`**，消灭重复定义。

**② group_id 显式贯穿（删掉所有反查）**
group_id **本就已被上下文携带**——`ctx.group_id`、工具 `context["group_id"]`、技能层 `list_skills_all(member_id, group_id=…)` 都有它，只是没传进路径函数。
- 改：VFS（`read_file/write_file/edit_file/list_workspace/make_dir/delete_path/bot_workspace`）、skills（discovery/loader/lifecycle）、shell 沙箱，统统加 `group_id` 形参，值从已携带它的 context 取。
- **副产物（重要）**：`_get_effective_ws` 里现有的 `SELECT group_id FROM members` 反查可**直接删除**（group_id 由入参传入）。正路是**移除**现有 DB-in-path hack，而非新增。

**③ API 边界解析一次**
前端走 `/api/members/{member_id}/...`，URL 只有 member_id。
- 改：在 HTTP handler 入口解析一次 group_id。**这些 handler 本就已 `bot = await get_member(db, member_id)`**，返回的 `bot` 字典自带 `group_id` → 直接 `bot["group_id"]` 往下传，**零新增查询**。不改路由。
- 这是合法的"边界建立上下文"：一次、在门口、显式下传；与"每个叶子函数偷偷查库"有本质区别。

### 8.1 连带改动

1. **路径模式扫描**：全仓扫写死 `bot_{` 的正则/拼接，统一改：
   - `SkillWatcher._BOT_RE`：`^bot_(\d+)/skills/` → `^group_(\d+)/bots/bot_(\d+)/skills/`（member_id 取第二捕获组）。
   - `clear_group_locks` 等以 `group_workspace(gid).parent` 为前缀的逻辑：新布局下 `group_{gid}/` 仍同时含 `bots/`+`shared/`，前缀清理语义不变，复核即可。

2. **共享区路径重定向**（`_get_effective_ws`）：**移除 `deliverables/` 前缀**，新增共享前缀 `workspace/`、`docs/` → 重定向到 `group_shared_dir(gid)`；`_SHARED_FILES` 保留。bot 用 `workspace/repo1/…`、`docs/…` 即落群组共享区，其余落私有。

3. **shell 沙箱放行 group 共享区**（`_resolve_shell_cwd` + `_check_shell_command_paths`）：除 `bot_dir(gid, bot_id)` 外额外放行 `group_shared_dir(gid)`，否则 Dev/QA 无法在共享区 `run_shell` build/跑测/git。需确认 `git clone/branch/commit/push` + 网络不被高危命令 guard 误杀。

4. **可发现性**（`list_workspace` / 启动上下文）：让 bot 看到本群组 `shared/`（含 `docs/`、`workspace/`）存在。

5. **git 凭证**：系统层已统一配置（Win/Linux 一致），直接取用，不按群组单配。

6. **Dev↔QA 交接时序**：无并发约束——角色顺序执行，同一时刻仅一个 bot 操作工作树，无锁冲突，无需额外编排。

### 8.2 迁移（一次性脚本）

- 跑前整体备份 `workspaces/`。
- 遍历 DB 中的 bot（按不变量必有 group）：`workspaces/bot_{id}` → `workspaces/group_{gid}/bots/bot_{id}`。
- 磁盘上无 DB 记录的 `bot_*` 目录 = 改造前脏数据，按不变量本不该存在，**直接删除**（不归档、不兼容）。

---

## 九、本设计修复的现状问题

- **Dev→QA 交接打通**：双方对 `shared/workspace/<repo>/` 同一 checkout 读写，QA 能测到 Dev 的代码。
- **隔离语义干净**：bot 收归群组下，`group_{id}/` 成单一隔离单元。
- **无界增长止血**：`runs/` 定性为易逝日志并加保留策略。
- **职责分明**：代码（git 树）/ 文档（`docs/`）/ 协调件（固定文件）/ 私有（bot 区）/ 易逝（runs）各归各位，互不污染。
