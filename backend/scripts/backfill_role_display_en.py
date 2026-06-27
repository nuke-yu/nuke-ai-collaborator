# backend/scripts/backfill_role_display_en.py
"""一次性回填：给 role.yaml 补 display_name_en，使角色展示名支持中英文。

背景：role.yaml 原本只有单个 display_name，群按群语言（默认 zh）灌入，所以群里
存的是中文名；UI 切英文时角色下拉仍是中文。本脚本把两套模板与现有群的 role.yaml
统一成双语约定：display_name=中文基准、display_name_en=英文（按目录名从 en 模板取）。
角色身份（目录名）不变。

约定（统一后每个 role.yaml）：
  display_name:    <中文>      # 基准；lang!=en 时用
  display_name_en: <英文>      # lang==en 时优先，缺则回退 display_name→目录名

步骤（幂等）：
  A. templates/zh/roles/<N>：补 display_name_en（保留 display_name 等其它字段）
  B. templates/en/roles/<N>：display_name←中文、display_name_en←英文（en 模板原本
     display_name 是英文，归一到约定；保留 avatar_color/system_prompt）
  C. 现有各群 group_<g>/roles/<N>：缺 display_name_en 则补（按目录名匹配 en 模板）；
     模板没有的自治角色（CEO 等）无英文名，跳过 → 回退中文/目录名

用法：
    python3 -m scripts.backfill_role_display_en          # dry-run
    python3 -m scripts.backfill_role_display_en --apply  # 执行
"""
from __future__ import annotations
import sys
from pathlib import Path

from skills.role_meta import read_role_meta, write_role_meta
from workspace import layout


def _display(role_dir: Path) -> str | None:
    meta = read_role_meta(role_dir)
    return meta.get("display_name") if meta else None


def _name_maps(root: Path) -> tuple[dict[str, str], dict[str, str]]:
    """目录名 → 中文名 / 英文名（取自 templates/zh、templates/en）。"""
    zh_root = root / "templates" / "zh" / "roles"
    en_root = root / "templates" / "en" / "roles"
    zh = {d.name: _display(d) for d in zh_root.iterdir() if d.is_dir()} if zh_root.exists() else {}
    en = {d.name: _display(d) for d in en_root.iterdir() if d.is_dir()} if en_root.exists() else {}
    return ({k: v for k, v in zh.items() if v}, {k: v for k, v in en.items() if v})


def _merge(role_dir: Path, *, display_name: str | None, display_name_en: str | None,
           force_display: bool, dry_run: bool) -> bool:
    """把 display_name/display_name_en 合并进 role_dir/role.yaml。返回是否有改动。
    force_display=False 时只在缺失才设 display_name；display_name_en 同理只补不覆盖。"""
    meta = dict(read_role_meta(role_dir) or {})
    changed = False
    if display_name and (force_display or not meta.get("display_name")):
        if meta.get("display_name") != display_name:
            meta["display_name"] = display_name
            changed = True
    if display_name_en and not meta.get("display_name_en"):
        meta["display_name_en"] = display_name_en
        changed = True
    if changed and not dry_run:
        write_role_meta(role_dir, meta)
    return changed


def run(root: Path, *, dry_run: bool) -> dict:
    zh_name, en_name = _name_maps(root)
    touched = {"zh_tpl": [], "en_tpl": [], "groups": {}}

    # A. zh 模板：补英文名
    zh_root = root / "templates" / "zh" / "roles"
    for d in sorted(zh_root.iterdir()) if zh_root.exists() else []:
        if d.is_dir() and _merge(d, display_name=None, display_name_en=en_name.get(d.name),
                                 force_display=False, dry_run=dry_run):
            touched["zh_tpl"].append(d.name)

    # B. en 模板：display_name 归一为中文、补英文名
    en_root = root / "templates" / "en" / "roles"
    for d in sorted(en_root.iterdir()) if en_root.exists() else []:
        if d.is_dir() and _merge(d, display_name=zh_name.get(d.name),
                                 display_name_en=en_name.get(d.name),
                                 force_display=True, dry_run=dry_run):
            touched["en_tpl"].append(d.name)

    # C. 现有各群：补英文名（仅模板里有对应英文的角色）
    from db import connect_sync
    with connect_sync() as conn:
        gids = [int(r[0]) for r in conn.execute("SELECT id FROM groups").fetchall()]
    for gid in gids:
        groot = layout.group_roles_dir(gid)
        if not groot.exists():
            continue
        names = []
        for d in sorted(groot.iterdir()):
            if d.is_dir() and _merge(d, display_name=None, display_name_en=en_name.get(d.name),
                                     force_display=False, dry_run=dry_run):
                names.append(d.name)
        if names:
            touched["groups"][gid] = names
    return touched


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    apply = "--apply" in argv
    from skills.constants import WORKSPACE_ROOT
    root = Path(WORKSPACE_ROOT)
    print(f"[回填] 工作区根: {root}")
    print(f"[回填] 模式: {'APPLY（写盘）' if apply else 'DRY-RUN（只打印）'}")
    res = run(root, dry_run=not apply)
    print(f"  A zh 模板补英文名: {res['zh_tpl']}")
    print(f"  B en 模板归一: {res['en_tpl']}")
    print(f"  C 各群补英文名: {res['groups']}")
    if not apply:
        print("\n[回填] dry-run 完成。确认无误后加 --apply 执行。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
