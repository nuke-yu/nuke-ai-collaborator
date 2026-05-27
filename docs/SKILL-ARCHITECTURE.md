# Skill 系统架构 · 全景设计

> 最后更新：2026-05-26
> 状态：设计阶段（未实现）

---

## 一、四层目录结构

```
workspaces/
│
├── system/skills/                     ◄── L1 GENERAL（平台内置）
│   ├── read-file.md                       所有 Bot 只读，随代码发布
│   ├── write-file.md                      创建方：开发者
│   ├── search-code.md                     激活：自动，always 或懒加载
│   ├── create-skill.md
│   └── run-tests.md
│
├── roles/                             ◄── L3 ROLE（角色专属）
│   ├── developer/skills/                  跟角色走，不跟 Bot 走
│   │   ├── code-review.md                 Bot 创建时按 role 自动复制
│   │   ├── write-unit-test.md             创建方：平台预置 + 用户扩展
│   │   ├── commit-and-pr.md
│   │   └── debug-error.md
│   ├── pm/skills/
│   │   ├── write-spec.md
│   │   └── update-board.md
│   └── qa/skills/
│       └── test-plan.md
│
├── bot_{id}/skills/                   ◄── Bot 私有层
│   ├── [用户手写的个人技能]                用户直接创建，立即生效
│   └── learned/                       ◄── L4 LEARNED（自学沉淀）
│       ├── draft/                         Bot 写入区（不自动生效）
│       │   └── project-conventions.md
│       └── active/                        用户审批后移入（才注入）
│           └── team-preferences.md
│
└── group_{id}/shared/skills/          ◄── L2 GROUP（群组领域）
    ├── dev-setup.md                       群组内所有 Bot 可读
    ├── run-tests.md                       创建方：用户 / 群组内 Bot
    ├── deploy.md
    └── api-conventions.md
```

---

## 二、层级定义

| 层级 | 路径 | 归属 | 内容定位 | 写入方 |
|---|---|---|---|---|
| **L1 General** | `workspaces/system/skills/` | 系统级，全局只读 | 通用能力：文件读写、创建 skill、搜索代码 | 平台内置 |
| **L2 Group** | `group_{id}/shared/skills/` | 群组领域，群组内共享 | 领域通用流程：环境搭建、测试命令、发布规范 | 用户 / Bot |
| **L3 Role** | `workspaces/roles/{role}/skills/` | 角色专属，跟角色走不跟 Bot 走 | 角色能力：Developer → code-review；PM → write-spec | Bot 创建时按 role 自动生成 |
| **L4 Learned** | `bot_{id}/skills/learned/active/` | Bot 个人沉淀，可控扩展 | Bot 自学的项目规律、团队偏好 | Bot 写 draft/，用户审批后移入 active/ |

---

## 三、Skill 文件结构

### 平铺文件（简单技能）

```
skills/
└── code-review.md
```

```markdown
---
name: code-review
description: 对代码进行系统性 Review，输出问题清单和改进建议
layer: role
role: developer
always: false
when_to_use: 当用户提交代码要求 review 时
status: active
learns: false
max_iterations: 5
---

## Code Review 流程

1. 读取目标文件
2. 检查代码规范、逻辑错误、安全隐患
3. 输出结构化问题清单
```

### 目录结构（复杂技能）

```
skills/
└── commit-and-pr/
    ├── SKILL.md        ← 主入口文件
    ├── steps.md        ← 子步骤文档
    └── templates/
        └── pr-body.md  ← PR 模板
```

### Frontmatter 字段全览

| 字段 | 类型 | 说明 | 里程碑 |
|---|---|---|---|
| `name` | string | Skill 显示名 | M1 ✅ |
| `description` | string | 注入 skill 列表时使用 | M1 ✅ |
| `always` | bool | `true` 则全文常驻 system prompt | M1 ✅ |
| `when_to_use` | string | 帮助模型判断调用时机 | M2 ✅ |
| `max_iterations` | int | 执行时动态扩展循环上限 | M1 ✅ |
| `status` | enum | `active` / `disabled` / `deprecated` | M2 |
| `layer` | enum | `system` / `group` / `role` / `personal` / `learned` | M2 |
| `role` | string | L3 技能归属的角色 | M2 |
| `learns` | bool | 执行后允许 Bot 写回总结到 draft/ | M3 |
| `user-invocable` | bool | `false` 则不出现在 skill 列表，只能 always 激活 | M3 |
| `paths` | string | gitignore 语法，文件路径匹配时自动激活 | M3 |
| `context` | enum | `inline`（默认）/ `fork`（独立子 Agent 执行） | M3 |
| `allowed-tools` | list | 执行时允许使用的工具白名单 | M3 |
| `model` | string | 执行时覆盖模型选择 | M4 |

---

## 四、生命周期状态机

### L1 / L2 / L3 / 用户手写技能

```
创建
 │
 ▼
active ◄──────── re-enable
 │
 │ disable
 ▼
disabled
 │
 │ archive
 ▼
deprecated
```

### L4 Bot 自学技能（两段式审批）

```
Bot 发现可复用规律
 │
 │ write_file → learned/draft/（不注入 prompt）
 ▼
draft
 ├── 用户拒绝 ──► 删除
 │
 │ 用户审批
 ▼
active/（开始注入 prompt）
 │
 │ 用户禁用
 ▼
disabled
```

**触发条件（Bot 何时写 draft）：**
- 用户显式说「记住这个做法」/「把这个加入你的技能」
- Skill frontmatter 声明 `learns: true`，执行后允许写回总结

---

## 五、运行时加载顺序

`list_skills()` 按以下顺序扫描，后层同名 skill 覆盖前层：

```
L1  system/skills/
     ↓ 合并
L2  group_{id}/shared/skills/
     ↓ 合并
L3  roles/{bot.role}/skills/
     ↓ 合并
L4  bot_{id}/skills/learned/active/
     ↓ 合并
    bot_{id}/skills/（用户手写，非 learned）
     ↓
     ┌──────────────────────────────────────┐
     │         最终 skill 列表               │
     │                                      │
     │  always: true  → 全文注入 system      │
     │  always: false → XML 元数据懒加载     │
     │  status: disabled → 跳过             │
     └──────────────────────────────────────┘
```

---

## 六、前端管理入口

```
Skill 库面板
├── System（L1）      只读，平台内置，可查看不可编辑
├── Group（L2）       可新增、编辑、删除；按群组隔离
├── Role（L3）        可新增、编辑；按角色分组展示
├── Personal          用户手写私有技能，立即生效
└── Learned（L4）     ⚠️ 待审批队列
                          ├── 确认 → 移入 active/
                          └── 拒绝 → 删除 draft
```

**每个 Skill 的操作：**

| 操作 | L1 | L2 | L3 | L4 |
|---|---|---|---|---|
| 查看 | ✅ | ✅ | ✅ | ✅ |
| 创建 | ❌ | ✅ | ✅ | Bot 自动（draft） |
| 编辑 | ❌ | ✅ | ✅ | ✅（active 后） |
| 启用/禁用 | ✅ | ✅ | ✅ | ✅ |
| 测试运行 | ✅ | ✅ | ✅ | ✅ |
| 审批 | — | — | — | ✅（必须） |
| 删除 | ❌ | ✅ | ✅ | ✅ |

---

## 七、实现路线图

| 功能 | 里程碑 | 说明 |
|---|---|---|
| L1 系统技能目录 + 内置示例 | M2 | `system/skills/` + 首批通用技能文件 |
| L2 群组技能目录 | M2 | `group_{id}/shared/skills/` 初始化，load_context_files 扩展 |
| L3 角色技能模板库 + 自动生成 | M2 | `roles/` 目录 + Bot 创建时按 role 写入 |
| `status` / `layer` frontmatter 字段 | M2 | list_skills() 解析并过滤 disabled |
| list_skills() 四层合并逻辑 | M2 | 按 L1→L2→L3→L4 扫描，同名后覆前，每条带 layer/status/injected |
| Session 注入事件广播 | M2 | tool_loop_v1 执行时广播 skill_loaded 事件（全文/元数据/未注入） |
| Skill 状态面板 UI | M2 | 展示层级、status、本次注入状态；enable/disable 切换；L4 审批队列 |
| L4 draft/active 两段式审批 | M3 | write_file 限制写入 draft/；前端审批队列 |
| `learns: true` frontmatter | M3 | 执行后触发写回 draft/ |
| Skill 管理 UI（浏览 + 编辑 + 审批） | M3 | 前端 Skill 库完整面板 |
| Skill 测试运行 | M3 | 单独执行一次 skill 看输出 |

---

## 八、参考实现

| 框架 | 参考点 | 本地路径 |
|---|---|---|
| claude-code-haha | `user/project/managed` 三级 scope；`paths:` 条件激活；`clearSkillCaches()` 热更新 | `/Users/Nuke/claude-code-haha-main/src/skills/` |
| opencode | 外部 URL skill 源；config-driven 路径扩展 | `/Users/Nuke/opencode/packages/opencode/src/skill/` |
| openclaw | `preemptive-compaction`；skill-per-agent-type 过滤 | `/Users/Nuke/openclaw-main/src/agents/` |
| gsd-2 | `skill-catalog` 安装机制；`skill-manifest` 按工作流类型过滤 | `/Users/Nuke/gsd-2/src/resources/extensions/gsd/` |

---

## 九、设计借鉴点（来自四框架生命周期分析）

| 借鉴点 | 来源 | 应用到我们的设计 |
|---|---|---|
| settings-first + 异步物化 | claude-code-haha | `status` 字段存 frontmatter，加载时读取；状态变更写文件，不依赖内存 |
| policy gates（allow / deny / global） | openclaw | L4 审批机制的 draft / active 两态；system 层只读不可禁用 |
| 查询时过滤而非加载时过滤 | opencode | `list_skills_all()` 返回全集含 disabled，调用方（tool_loop / UI）按需过滤 |
| 9 步卸载状态机 | openclaw | L4 draft → reject 流程参考；未来 skill 删除需清理 frontmatter + 文件 + 缓存 |
| 版本目录 + orphan 7 天清理 | claude-code-haha | skill 编辑历史归档设计参考（M3）；draft 被拒绝后可软删除而非立即清除 |
| Catalog 按技术栈匹配 | gsd-2 | L3 Role Skill 初始化时按 bot.role 自动匹配预置包（developer / pm / qa 等） |
