# Release Note — Skill 权限收紧（blanket run_skill allow → name-scoped）

**日期：** 2026-06-27
**影响：** 任何曾经对某 bot「always allow run_skill」的群组。

## 变了什么

`synthesize_args_pattern` 从 blanket 改为 name-scoped（Plan A）。旧的「always allow
某技能」在引擎里其实被存成 `args_pattern=''` —— 放行**该 bot 的所有技能名**。Plan B
上线 external skill 的 git 导入后，这种 blanket allow 会**静默自动批准**新导入的（未受信）
外部技能执行。这是必须堵的洞。

## 必须执行的部署步骤

部署 Plan A/B 后、开放 external 导入前，对**中央 DB**跑一次迁移：

```bash
# 1) 停机 + 备份中央 DB
# 2) dry-run 看计划
python3 -m scripts.migrate_skill_assignment
# 3) 确认无误后执行
python3 -m scripts.migrate_skill_assignment --apply
```

迁移会把每个 bot 的 blanket `run_skill` allow 展开为它**当时实际可运行**的各技能的
显式 name-scoped allow，再删掉 blanket 规则：既有技能的免审批保留，未来/导入技能不再
被自动放行。

## 回滚

脚本默认 dry-run、不静默、幂等。回滚 = 从步骤 1 的备份恢复中央 DB。重复运行安全：
没有 blanket 规则的 bot 会被跳过。

## 不在本迁移内

- `bot_skills` 不填充非外部技能（filter_visible 只 gate 外部层；详见 Plan C 计划设计说明）。
- `tool_pattern='*'`（放行全部工具）的 allow-all bot 不在覆盖内：那是 operator 有意的宽
  策略，不是「always allow 某技能」误存出来的产物。要收紧此类 bot 是另一个独立的安全决策。
- external 导入/分配的运营本身由 Plan B 的 API/UI 负责，与本迁移无关。
