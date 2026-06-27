"""角色目录枚举：列出某 roles 根下的角色目录 + role.yaml 元数据 + 技能数。

纯读、无副作用。被 /api/templates/roles、/api/groups/{id}/roles 与 add_member
校验共用。discovery 永不读 role.yaml；角色元数据只走 read_role_meta。"""
from __future__ import annotations
from pathlib import Path

from .role_meta import read_role_meta


def list_role_catalog(roles_root: Path, lang: str = "zh") -> list[dict]:
    """列出 roles_root/* 角色目录。返回按 role 名排序的
    [{role, display_name, avatar_color, system_prompt, skill_count}]。
    根不存在 → []。

    role 是角色身份（目录名），与语言无关。display_name 按 lang 解析：
    lang=='en' → display_name_en 优先，缺则回退基准 display_name → 目录名；
    其它 → 基准 display_name → 目录名。"""
    out: list[dict] = []
    if not roles_root.exists():
        return out
    for d in sorted(roles_root.iterdir()):
        if not d.is_dir():
            continue
        meta = read_role_meta(d) or {}
        base = meta.get("display_name") or d.name
        display = (meta.get("display_name_en") or base) if lang == "en" else base
        skills_dir = d / "skills"
        skill_count = len(list(skills_dir.glob("*.md"))) if skills_dir.exists() else 0
        out.append({
            "role": d.name,
            "display_name": display,
            "avatar_color": meta.get("avatar_color"),
            "system_prompt": meta.get("system_prompt"),
            "skill_count": skill_count,
        })
    return out
