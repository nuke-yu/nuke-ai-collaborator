# backend/scripts/migrate_role_skills.py
"""一次性迁移：把老 workspaces/roles/ + role_templates 表迁成全局角色模板，
并对齐现有群与 bot。沿用 migrate_workspace_layout 的纪律。

安全约定：
- **跑前停机 + 备份** workspaces/ 与中央 DB（脚本不替你备份）。
- 默认 dry-run：只打印计划，不动盘。加 --apply 才执行。
- 幂等：已建模板 / 已灌群 / 已改名 → 跳过，可重复运行。

步骤：
  A. 建全局 zh 模板  templates/zh/roles/<role>/{role.yaml, skills/}
  A2. 建全局 en 模板骨架（role.yaml + 占位 skills，正文后续补）
  B. 给现有群灌角色（按群语言拷模板，复用 provision_group_roles）
  C. 对齐现有 bot 的自由文本 role（不命中→在该群自动建同名空角色）
  D. 退役老全局目录  roles/ → roles.legacy/

用法：
    python3 -m scripts.migrate_role_skills            # dry-run
    python3 -m scripts.migrate_role_skills --apply    # 执行
"""
from __future__ import annotations
import shutil
import sys
from pathlib import Path

from skills.role_meta import write_role_meta

# 丢弃的老英文残留目录（与中文角色重复，role_templates 无对应行）
DISCARD = {"developer", "qa", "pm"}

# 新增角色：role -> [(源角色目录名, 技能名)]，技能正文从既有 .md 取。
NEW_ROLES: dict[str, list[tuple[str, str]]] = {
    "Architecture": [("系统架构师", "design-architecture"), ("系统架构师", "tech-stack-review")],
    "PM": [("需求分析师", "write-spec"), ("pm", "update-board"), ("需求分析师", "write-user-story")],
}

# 12 角色的英文 display_name（en 模板套用）。键为磁盘角色目录名。
EN_DISPLAY: dict[str, str] = {
    "代码助手": "Code Assistant",
    "后端Python专家": "Backend Python Expert",
    "后端Java工程师": "Backend Java Engineer",
    "前端工程师": "Frontend Engineer",
    "系统架构师": "System Architect",
    "需求分析师": "Requirements Analyst",
    "QA测试工程师": "QA Engineer",
    "DevOps工程师": "DevOps Engineer",
    "写作助手": "Writing Assistant",
    "翻译专家": "Translation Expert",
    "Architecture": "Architecture",
    "PM": "PM",
}

# 新角色无 role_templates 行，给个最小元数据（avatar 复用近义角色色）。
NEW_ROLE_META: dict[str, dict] = {
    "Architecture": {"avatar_color": "#8b5cf6"},
    "PM": {"avatar_color": "#0ea5e9"},
}


def synth_role_yaml(dst_dir: Path, role: str, db_meta: dict | None, *,
                    display_name: str | None = None) -> None:
    """落 dst_dir/role.yaml。display_name 缺省取 role；db_meta 提供 system_prompt/avatar。

    avatar_color 解析：db_meta 的值优先，否则回退 NEW_ROLE_META（Architecture/PM 这类
    新角色没有 role_templates 行，db_meta 恒为 None，仍应拿到各自默认头像色）。既无
    db_meta 又不在 NEW_ROLE_META 的角色（如 step C 自动建的空角色 CEO）→ avatar/prompt 均 None。
    """
    db_meta = db_meta or {}
    meta = {
        "display_name": display_name or role,
        "avatar_color": db_meta.get("avatar_color") or NEW_ROLE_META.get(role, {}).get("avatar_color"),
        "system_prompt": db_meta.get("system_prompt"),
    }
    write_role_meta(dst_dir, meta)


def _copy_skill_md(src_md: Path, dst_skills: Path, *, dry_run: bool) -> None:
    if dry_run or not src_md.exists():
        return
    dst_skills.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_md, dst_skills / src_md.name)


def build_zh_templates(root: Path, role_db_meta: dict[str, dict], *,
                       dry_run: bool) -> dict:
    """Step A：从 root/roles/* 建 templates/zh/roles/*（保留 10 角色 + Architecture/PM）。"""
    roles_src = root / "roles"
    zh_root = root / "templates" / "zh" / "roles"
    built: list[str] = []

    # 10 个保留的中文角色：磁盘有、且不在 DISCARD
    if roles_src.exists():
        for src in sorted(roles_src.iterdir()):
            if not src.is_dir() or src.name in DISCARD:
                continue
            role = src.name
            dst = zh_root / role
            for md in sorted((src / "skills").glob("*.md")) if (src / "skills").exists() else []:
                _copy_skill_md(md, dst / "skills", dry_run=dry_run)
            if not dry_run:
                synth_role_yaml(dst, role, role_db_meta.get(role))
            built.append(role)

    # 新角色：技能正文从既有角色 .md 取
    for role, sources in NEW_ROLES.items():
        dst = zh_root / role
        for src_role, skill in sources:
            _copy_skill_md(roles_src / src_role / "skills" / f"{skill}.md",
                           dst / "skills", dry_run=dry_run)
        if not dry_run:
            synth_role_yaml(dst, role, role_db_meta.get(role))
        built.append(role)

    return {"built": built, "dry_run": dry_run}


def _frontmatter_only(md_text: str, skill_name: str) -> str:
    """取 zh 技能的 YAML frontmatter，正文换成英文占位（TODO）。

    没有 frontmatter（首行非 '---'）时，合成最小 frontmatter。"""
    lines = md_text.splitlines()
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if end is not None:
            fm = "\n".join(lines[: end + 1])
            return f"{fm}\n\nTODO: English skill body for '{skill_name}'.\n"
    return f"---\nname: {skill_name}\ndescription: TODO\n---\n\nTODO: English skill body for '{skill_name}'.\n"


def build_en_skeletons(root: Path, *, dry_run: bool) -> dict:
    """Step A2：按 zh 模板套建 en 骨架（en display_name + 占位技能正文）。"""
    zh_root = root / "templates" / "zh" / "roles"
    en_root = root / "templates" / "en" / "roles"
    built: list[str] = []
    if not zh_root.exists():
        return {"built": built, "dry_run": dry_run}

    from skills.role_meta import read_role_meta

    for zdir in sorted(zh_root.iterdir()):
        if not zdir.is_dir():
            continue
        role = zdir.name
        edir = en_root / role
        if not dry_run:
            (edir / "skills").mkdir(parents=True, exist_ok=True)
            zmeta = read_role_meta(zdir) or {}
            synth_role_yaml(
                edir, role,
                {"system_prompt": zmeta.get("system_prompt"),
                 "avatar_color": zmeta.get("avatar_color")},
                display_name=EN_DISPLAY.get(role, role),
            )
            for md in sorted((zdir / "skills").glob("*.md")) if (zdir / "skills").exists() else []:
                (edir / "skills" / md.name).write_text(
                    _frontmatter_only(md.read_text(encoding="utf-8"), md.stem),
                    encoding="utf-8")
        built.append(role)
    return {"built": built, "dry_run": dry_run}


def seed_existing_groups(root: Path, group_ids: list[int], *, dry_run: bool) -> dict:
    """Step B：给现有群按语言灌角色（复用 provision_group_roles，幂等）。"""
    seeded: dict[int, list[str]] = {}
    if not dry_run:
        from workspace.role_provision import provision_group_roles
        for gid in group_ids:
            seeded[gid] = provision_group_roles(gid)
    else:
        for gid in group_ids:
            seeded[gid] = []  # dry-run：只列出待灌的群，不动盘
    return {"seeded": seeded, "dry_run": dry_run}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    apply = "--apply" in argv

    from skills.constants import WORKSPACE_ROOT
    root = Path(WORKSPACE_ROOT)

    print(f"[迁移] 工作区根: {root}")
    print(f"[迁移] 模式: {'APPLY（写盘）' if apply else 'DRY-RUN（只打印）'}")
    if apply:
        print("[迁移] 确认：已停机且已备份 workspaces/ 与中央 DB ？(Ctrl-C 取消)")

    # 各 step 在 Task 5-9 接入；本脚手架版 dry-run 不动盘。
    if not apply:
        print("\n[迁移] dry-run 完成。确认无误后加 --apply 执行。")
        return 0
    print("\n[迁移] （步骤尚未接入，见后续任务）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
