"""role.yaml 元数据读写（display_name / display_name_en / avatar_color / system_prompt）。

display_name 为基准（中文）展示名，display_name_en 为英文展示名（可选，缺则回退
display_name → 目录名）。角色身份始终是目录名，与展示名无关。

discovery 永不读 role.yaml；它只列 skills/。角色元数据只由本模块与上层
roles/templates API 读取。"""
from __future__ import annotations
from pathlib import Path

import yaml

_FIELDS = ("display_name", "display_name_en", "avatar_color", "system_prompt")


def read_role_meta(role_dir: Path) -> dict | None:
    """读 role_dir/role.yaml → {display_name, display_name_en, avatar_color, system_prompt}
    （缺字段为 None）。文件不存在 / 解析失败 → None。"""
    fp = role_dir / "role.yaml"
    if not fp.exists():
        return None
    try:
        data = yaml.safe_load(fp.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return {k: data.get(k) for k in _FIELDS}


def write_role_meta(role_dir: Path, meta: dict) -> None:
    """写 role_dir/role.yaml：仅已知字段中非 None 的项，保序、允许中文。"""
    role_dir.mkdir(parents=True, exist_ok=True)
    out = {k: meta[k] for k in _FIELDS if meta.get(k) is not None}
    (role_dir / "role.yaml").write_text(
        yaml.safe_dump(out, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
