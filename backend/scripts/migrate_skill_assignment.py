# backend/scripts/migrate_skill_assignment.py
"""一次性迁移：把旧的 blanket `run_skill` allow 权限规则收紧为按技能名的
name-scoped allow 规则。沿用 migrate_role_skills / migrate_workspace_layout 的纪律。

背景（spec §10）：Plan A 把 `synthesize_args_pattern` 从 blanket 改成 name-scoped。
旧的「always allow 某技能」其实在引擎里被存成 args_pattern='' = 放行**所有**技能名。
Plan B 上线 external skill 导入后，这种 blanket allow 会**静默自动批准**新导入的
（未受信）外部技能执行。本迁移把每个 bot 的 blanket run_skill allow 展开为它**当时
实际可运行**的各技能的显式 name-scoped allow，再删掉 blanket 规则——保留既有技能的
免审批，同时堵住「自动放行未来/导入技能」的洞。

bot_skills 不在本迁移内填充：filter_visible 只 gate 外部层技能，迁移时无外部技能，
且 Plan B 的分配面板 reconcile 会把非外部行清掉（见 plan 设计说明）。

安全约定：
- **跑前停机 + 备份中央 DB**（脚本不替你备份）。
- 默认 dry-run：只打印计划，不动盘。加 --apply 才执行。
- 幂等：无 blanket 规则的 bot 跳过；已存在的 name-scoped 规则跳过；可重复运行。

用法：
    python3 -m scripts.migrate_skill_assignment            # dry-run
    python3 -m scripts.migrate_skill_assignment --apply    # 执行
"""
from __future__ import annotations
import sys

import db as _db
from permissions import db as pdb
from permissions.models import Rule
from permissions.patterns import synthesize_args_pattern
from skills.discovery import list_skills_all

# tool_pattern 值视为「针对 run_skill 的 blanket」。纯通配 '*'/'**'（放行全部工具）
# 是 operator 有意的宽策略，不在本技能迁移范围内，故排除。
_RUN_SKILL_PATTERNS = {"run_skill", "run_skill*"}


def is_blanket_run_skill_rule(rule: Rule) -> bool:
    return (
        rule.action == "allow"
        and rule.args_pattern == ""
        and rule.tool_pattern in _RUN_SKILL_PATTERNS
    )


async def plan_for_bot(bot_id: int, group_id: int | None, role: str | None) -> dict:
    """只读：算出该 bot 的展开计划，不写盘。"""
    rules = await pdb.load_rules(bot_id)
    blanket_ids = [r.id for r in rules if is_blanket_run_skill_rule(r)]
    plan = {"bot_id": bot_id, "blanket_rule_ids": blanket_ids,
            "add_patterns": [], "skipped_existing": []}
    if not blanket_ids:
        return plan

    # 已存在的 name-scoped allow（args_pattern 非空）→ 不重复加。
    existing = {r.args_pattern for r in rules
                if r.action == "allow" and r.tool_pattern in _RUN_SKILL_PATTERNS
                and r.args_pattern}

    skills = await list_skills_all(bot_id, group_id=group_id, role=role)
    seen: set[str] = set()
    for s in skills:
        pat = synthesize_args_pattern("run_skill", {"name": s["name"]})
        if not pat or pat in seen:
            continue
        seen.add(pat)
        if pat in existing:
            plan["skipped_existing"].append(pat)
        else:
            plan["add_patterns"].append(pat)
    return plan


async def apply_for_bot(bot_id: int, group_id: int | None, role: str | None) -> dict:
    """写盘：按 plan 加 name-scoped allow，再删 blanket。幂等。"""
    plan = await plan_for_bot(bot_id, group_id, role)
    if not plan["blanket_rule_ids"]:
        return {**plan, "added": 0, "deleted": 0}
    for pat in plan["add_patterns"]:
        await pdb.save_rule(bot_id, "run_skill", pat, "allow")
    for rid in plan["blanket_rule_ids"]:
        await pdb.delete_rule(rid)
    return {**plan, "added": len(plan["add_patterns"]),
            "deleted": len(plan["blanket_rule_ids"])}


async def _load_bots() -> list[tuple[int, int, str | None]]:
    async with _db.global_db() as db:
        async with db.execute(
            "SELECT id, group_id, role FROM members WHERE type='bot'"
        ) as cur:
            return [(r[0], r[1], r[2]) for r in await cur.fetchall()]


async def migrate(apply: bool) -> dict:
    bots = await _load_bots()
    out_bots: list[dict] = []
    total_added = total_deleted = 0
    for bot_id, group_id, role in bots:
        plan = await plan_for_bot(bot_id, group_id, role)
        if not plan["blanket_rule_ids"]:
            continue
        if apply:
            res = await apply_for_bot(bot_id, group_id, role)
            total_added += res["added"]
            total_deleted += res["deleted"]
            out_bots.append(res)
        else:
            out_bots.append(plan)
    return {"bots": out_bots, "apply": apply,
            "total_added": total_added, "total_deleted": total_deleted}


import asyncio


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    apply = "--apply" in argv

    print(f"[迁移] 模式: {'APPLY（写盘）' if apply else 'DRY-RUN（只打印）'}")
    if apply:
        print("[迁移] 确认：已停机且已备份中央 DB ？(Ctrl-C 取消)")

    result = asyncio.run(migrate(apply=apply))

    if not result["bots"]:
        print("[迁移] 没有需要收紧的 blanket run_skill allow 规则。无操作。")
        return 0

    for p in result["bots"]:
        verb = "已加" if apply else "将加"
        print(f"  bot {p['bot_id']}: {verb} name-scoped allow {p['add_patterns']}"
              f"；blanket 规则 {p['blanket_rule_ids']}"
              f"（已覆盖跳过: {p['skipped_existing']}）")
    if apply:
        print(f"[迁移] 完成：新增 {result['total_added']} 条 name-scoped allow，"
              f"删除 {result['total_deleted']} 条 blanket。")
    else:
        print("\n[迁移] dry-run 完成。确认无误后加 --apply 执行。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
