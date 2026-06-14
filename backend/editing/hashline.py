"""editing/hashline.py — 行哈希锚点编辑（纯逻辑，无 IO）。

范式不同于文本匹配：read 时给每行打一个**内容哈希锚** `L<行号>#<hash>`，模型按锚引用要改
的行；编辑时用哈希定位——**即使该行因前面增删而位移，哈希不变、锚仍有效**（对标 gsd-2 的
hashline-edit）。适合大文件里精准定位单行/少数行，避开「重抄一大段 old_string」。

对外：
- annotate(content)            → 带锚的只读视图（给模型看）
- apply_anchored_edits(c, eds) → 按锚应用编辑，返回新内容（原子，冲突/失效即抛 HashlineError）
"""
import hashlib
import re

_ANCHOR_RE = re.compile(r"^\s*L?(\d+)?\s*#\s*([0-9a-fA-F]{4,})\s*$")


class HashlineError(Exception):
    """锚点编辑无法安全完成（锚失效 / 歧义 / 冲突 / 非法）。"""


def line_hash(line: str) -> str:
    """一行内容的短稳定哈希（取 sha1 前 7 位 hex）。对原始行做哈希——模型用的是我们在
    annotate 里给出的哈希，不自己重算，故不受其复现细节影响。"""
    return hashlib.sha1(line.encode("utf-8")).hexdigest()[:7]


def _split_body(content: str):
    """拆出正文行 + 是否有尾换行（最后那个空串不算一行）。"""
    lines = content.split("\n")
    trailing_nl = bool(lines) and lines[-1] == ""
    return (lines[:-1] if trailing_nl else lines), trailing_nl


def annotate(content: str) -> str:
    """只读视图：每行前缀 `L<n>#<hash>│ `。"""
    body, _ = _split_body(content)
    return "\n".join(f"L{i + 1}#{line_hash(ln)}│ {ln}" for i, ln in enumerate(body))


def parse_anchor(anchor: str):
    """解析锚：'L12#a3f0c1d' / '12#a3f0c1d' / '#a3f0c1d' → (行号|None, hash)。非法 → (None, None)。"""
    m = _ANCHOR_RE.match(anchor or "")
    if not m:
        return None, None
    return (int(m.group(1)) if m.group(1) else None), m.group(2).lower()


def apply_anchored_edits(content: str, edits: list) -> str:
    """按锚顺序应用编辑，返回新内容。

    每条 edit：{"anchor": "L<n>#<hash>", "op": "replace"|"delete"|"insert_after", "text": str}
    - 全部锚先对**原始内容**解析定位，再按行应用 → 原子，且互不受彼此影响。
    - 锚失效（哈希查无此行）/ 多行命中无法消歧 / 同一行被多条命中 → 抛 HashlineError。
    """
    if not edits:
        raise HashlineError("edits 为空")
    body, trailing_nl = _split_body(content)

    hmap: dict[str, list[int]] = {}
    for idx, ln in enumerate(body):
        hmap.setdefault(line_hash(ln), []).append(idx)

    ops: dict[int, tuple[str, str]] = {}
    for e in edits:
        line_no, h = parse_anchor(e.get("anchor", ""))
        if not h:
            raise HashlineError(f"非法 anchor: {e.get('anchor')!r}")
        idxs = hmap.get(h, [])
        if not idxs:
            raise HashlineError(f"anchor {e.get('anchor')!r} 已失效（该行内容已变或不存在）")
        if len(idxs) > 1:
            if line_no is not None and (line_no - 1) in idxs:
                idx = line_no - 1
            else:
                raise HashlineError(f"anchor {e.get('anchor')!r} 命中多行，请带行号消歧")
        else:
            idx = idxs[0]
        if idx in ops:
            raise HashlineError(f"第 {idx + 1} 行被多条 edit 命中，冲突")
        op = e.get("op", "replace")
        if op not in ("replace", "delete", "insert_after"):
            raise HashlineError(f"未知 op: {op!r}")
        ops[idx] = (op, e.get("text", ""))

    out: list[str] = []
    for idx, ln in enumerate(body):
        if idx not in ops:
            out.append(ln)
            continue
        op, text = ops[idx]
        if op == "replace":
            out.append(text)
        elif op == "insert_after":
            out.append(ln)
            out.append(text)
        # delete: 跳过该行

    result = "\n".join(out)
    if trailing_nl and result:
        result += "\n"
    return result
