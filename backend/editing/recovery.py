"""editing/recovery.py — edit 失配时的恢复辅助（纯逻辑，无 IO）。

两件事，都在 apply_replacement 抛 EditError(未找到) 之后由 IO 层调用：
- idempotent_skip: 目标已存在 → 视为已应用，把硬失败降级为幂等跳过；
- mismatch_hint:   回吐近邻上下文，帮模型重新锚定 old_string。
"""
import difflib

_HINT_LIMIT = 800


def idempotent_skip(content: str, old: str, new: str) -> bool:
    """new 已落地、old 不在文件里 → 判为「已应用」。

    保守：要求 new **恰好出现一次**（避免 new 是常见片段时误判），且 old 不在文件中
    （走到这里时 apply_replacement 已找不到 old，此处再确认精确不存在）。
    """
    new_s = new.strip()
    if not new_s:
        return False
    return content.count(new) == 1 and old not in content


def mismatch_hint(content: str, old: str, limit: int = _HINT_LIMIT) -> str:
    """old 定位失败时，回吐一段近邻上下文帮模型重新锚定。

    小文件（≤limit）直接回吐全文；大文件回吐与 old 首个非空行最相似那行附近的窗口。
    """
    if not content:
        return "[提示] 文件为空。"
    if len(content) <= limit:
        return "[提示] 未匹配；当前文件内容如下，请据此重取 old_string：\n" + content

    content_lines = content.split("\n")
    anchor = next((ln for ln in old.split("\n") if ln.strip()), "")
    best_i, best_ratio = 0, -1.0
    if anchor:
        a = anchor.strip()
        for i, ln in enumerate(content_lines):
            r = difflib.SequenceMatcher(None, a, ln.strip()).ratio()
            if r > best_ratio:
                best_i, best_ratio = i, r

    lo = max(0, best_i - 3)
    hi = min(len(content_lines), best_i + 4)
    window = "\n".join(content_lines[lo:hi])
    if len(window) > limit:
        window = window[:limit] + "\n…（已截断）"
    return (f"[提示] 未匹配；最相似处在第 {best_i + 1} 行附近，上下文如下，"
            f"请据此重取 old_string：\n{window}")
