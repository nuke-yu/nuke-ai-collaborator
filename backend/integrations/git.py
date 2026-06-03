"""integrations/git.py — Git/PR 接入（MVP1 用本地工件替身）。

LocalGitClient 把 PR 产出成一个工作区文件(prs/PR-N.md)并返回 stub 的 pr_id/url。
真 Git/平台接入时实现一个调用 Git API 的 GitClient 子类并 set_git() 即可。
"""
from abc import ABC, abstractmethod

from workspace import write_file


class GitClient(ABC):
    @abstractmethod
    async def create_pr(self, group_id: int, title: str,
                        description: str = "", ticket_ids: list[str] | None = None) -> dict:
        """提交一个 PR，返回 {pr_id, url, title, tickets}。"""


class LocalGitClient(GitClient):
    """替身：PR 落成工作区文件，pr_id/url 为本地占位。"""

    def __init__(self) -> None:
        self._counter: dict[int, int] = {}

    async def create_pr(self, group_id: int, title: str,
                        description: str = "", ticket_ids: list[str] | None = None) -> dict:
        tickets = ticket_ids or []
        n = self._counter.get(group_id, 0) + 1
        self._counter[group_id] = n
        pr_id = f"PR-{n}"
        refs = ", ".join(tickets) if tickets else "(无)"
        content = (
            f"# {pr_id}: {title}\n\n"
            f"关联工单: {refs}\n\n"
            f"{description}\n"
        )
        await write_file(0, f"prs/{pr_id}.md", content, group_id=group_id)
        return {"pr_id": pr_id, "url": f"local://prs/{pr_id}.md",
                "title": title, "tickets": tickets}


_client: GitClient = LocalGitClient()


def get_git() -> GitClient:
    return _client


def set_git(client: GitClient) -> None:
    global _client
    _client = client
