"""integrations/ — 外部系统接入点（Jira / Git…）。

MVP1 用本地替身实现(Local*Client)，但都藏在接口(*Client ABC)后面：真接入时
调 set_jira()/set_git() 换成调真实 REST/Git API 的实现，调用方(rd_tools / 编排层)
完全不用改。
"""
from integrations.jira import get_jira, set_jira, JiraClient, LocalJiraClient
from integrations.git import get_git, set_git, GitClient, LocalGitClient

__all__ = [
    "get_jira", "set_jira", "JiraClient", "LocalJiraClient",
    "get_git", "set_git", "GitClient", "LocalGitClient",
]
