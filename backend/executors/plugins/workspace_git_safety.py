"""Detection of git commands requiring human approval."""
from __future__ import annotations

_GIT_GLOBAL_OPTS_WITH_VALUE = frozenset({
    "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
})


def git_subcommand(rest: list[str]) -> tuple[str | None, list[str]]:
    i = 0
    while i < len(rest):
        token = rest[i]
        if token in _GIT_GLOBAL_OPTS_WITH_VALUE:
            i += 2
            continue
        if token.startswith("-"):
            i += 1
            continue
        return token, rest[i + 1:]
    return None, []


def destructive_git_reason(rest: list[str]) -> str | None:
    sub, args = git_subcommand(rest)
    if sub is None:
        return None
    argset = set(args)

    def has(*flags: str) -> bool:
        return any(flag in argset for flag in flags)

    if sub == "reset" and has("--hard"):
        return "git reset --hard 会丢弃已跟踪文件的未提交改动（不可恢复）"
    if sub == "clean" and (has("--force") or any(
        arg.startswith("-") and not arg.startswith("--") and "f" in arg
        for arg in args
    )):
        return "git clean -f 会删除未跟踪文件（git 从未存过，不可恢复）"
    if sub == "checkout" and (has("-f", "--force") or "." in args or "--" in args):
        return "git checkout 会丢弃工作树未提交改动"
    if sub == "restore" and "--staged" not in argset and (
        has("-f", "--force") or "." in args
    ):
        return "git restore 会丢弃工作树未提交改动"
    if sub == "push" and (
        has("--force", "-f", "--force-with-lease", "--mirror", "--delete", "-d")
        or any(arg.startswith("+") for arg in args)
    ):
        return "git push --force 会重写远端历史（影响每个克隆）"
    if sub == "gc" and any(
        arg.startswith("--prune=") and arg != "--prune=never" for arg in args
    ):
        return "git gc --prune 立即回收悬空对象，关闭恢复窗口"
    if sub == "reflog" and "expire" in args:
        return "git reflog expire 清空 reflog，关闭恢复窗口"
    if sub == "branch" and (has("-D") or (has("--delete") and has("--force"))):
        return "git branch -D 强制删除分支"
    if sub == "stash" and ("clear" in args or "drop" in args):
        return "git stash clear/drop 丢弃暂存内容"
    if sub == "filter-branch":
        return "git filter-branch 重写历史"
    if sub == "update-ref" and has("-d", "--delete"):
        return "git update-ref -d 删除引用"
    return None
