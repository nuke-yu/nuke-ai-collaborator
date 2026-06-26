"""建群拷贝：把全局角色模板按群语言拷进 group_<id>/roles/。

唯一拷贝原语是 SkillStore.copy(TemplateScope → RoleScope)；role.yaml 作为元数据
单独文件拷贝。幂等：群里已存在该角色目录则整体跳过（不覆盖群内自治内容）。
System 池（L1）不在此拷贝，是跨群共享引用。"""
from __future__ import annotations
import shutil

from workspace import layout
from skills.store import SkillStore
from skills.scope import TemplateScope, RoleScope


def provision_group_roles(group_id: int, lang: str | None = None) -> list[str]:
    """把 templates/<lang>/roles/* 拷进 group_<id>/roles/*。返回本次新建的角色名。"""
    if lang is None:
        lang = layout.get_group_language(group_id)
    templates_root = layout.templates_roles_dir(lang)
    if not templates_root.exists():
        return []

    store = SkillStore()
    provisioned: list[str] = []
    for tdir in sorted(templates_root.iterdir()):
        if not tdir.is_dir():
            continue
        role = tdir.name
        dst_dir = layout.group_roles_dir(group_id) / role
        if dst_dir.exists():
            continue  # 幂等：该角色已建过，跳过
        dst_dir.mkdir(parents=True, exist_ok=True)

        src_meta = tdir / "role.yaml"
        if src_meta.exists():
            shutil.copy2(src_meta, dst_dir / "role.yaml")

        src_scope = TemplateScope(lang, role)
        dst_scope = RoleScope(group_id, role)
        for entry in store.list(src_scope):
            store.copy(src_scope, entry["name"], dst_scope)
        provisioned.append(role)
    return provisioned
