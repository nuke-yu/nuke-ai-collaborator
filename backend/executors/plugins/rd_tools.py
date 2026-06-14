"""executors/plugins/rd_tools.py — RD 流水线工具（Jira / PR 替身）。

给 BA/Dev 在工作流里调用的结构化工具：
  - create_jira_ticket：BA 确认需求后建工单（含 AC）。
  - list_jira_tickets：Dev/QA 查看要做/要测的工单（只读）。
  - update_jira_ticket：Dev/QA 更新工单状态（backlog → in_progress → done）。
  - create_pr：Dev 自测后提 PR（替身），关联 Jira 工单。

工具产出落到 integrations 的本地替身(tickets 表 / 工作区文件)，真 Jira/Git 接入时
换 integrations 实现即可，这里不变。这些是内部记账型工具，人把关在工作流的 4 道门，
不在每次工具调用——故在 workspace_tools 的权限钩子里 auto-allow（见 _AUTO_ALLOW_TOOLS）。
"""
from executors.base import ToolDef
from executors import tool_executor
from integrations.jira import get_jira
from integrations.git import get_git

from pydantic import BaseModel, Field
from typing import Literal, Optional, List

class CreateJiraTicketParams(BaseModel):
    title: str = Field(..., description="工单标题")
    description: str = Field("", description="工单描述/范围")
    acceptance_criteria: str = Field("", description="验收标准(AC)，可多条")
    project: str = Field("", description="所属项目名（与 PROJECTS.md 中一致，如 my-app）。填写后 BOARD.md 自动显示项目列，Dev/QA 无需猜测。")

class ListJiraTicketsParams(BaseModel):
    pass

class UpdateJiraTicketParams(BaseModel):
    ticket_id: str = Field(..., description="工单号，如 DFT-1")
    status: Literal["backlog", "in_progress", "done"] = Field(..., description="新状态：backlog / in_progress / done")
    project: Optional[str] = Field(None, description="（可选）所属项目名。BA 建单时未填、或 Dev 自行命名时在此补写，写入后 BOARD.md 立即可见。")

class CreatePrParams(BaseModel):
    title: str = Field(..., description="PR 标题")
    description: str = Field("", description="PR 描述/改动说明")
    ticket_ids: List[str] = Field(default_factory=list, description="关联的 Jira 工单号，如 ['DFT-1','DFT-2']")

RD_TOOLS = [
    ToolDef(
        name="create_jira_ticket",
        description="创建一个 Jira 工单（含验收标准 AC）。BA 在需求确认后用它建工单。project 字段关联工单到具体项目，BOARD.md 会显示该列方便 Dev/QA 定位。",
        parameters=CreateJiraTicketParams,
    ),
    ToolDef(
        name="list_jira_tickets",
        description="列出本群当前所有 Jira 工单（标题/状态/AC）。Dev/QA 用它查看要做/要测什么。",
        parameters=ListJiraTicketsParams,
        concurrency_safe=True,
    ),
    ToolDef(
        name="update_jira_ticket",
        description=(
            "更新 Jira 工单状态。Dev 开始任务时标 in_progress，完成后标 done 并填写 project（"
            "若 BA 建工单时未填，Dev 根据需求自行命名后在此写入）。BOARD.md 自动更新 Project 列，"
            "QA 即可看到要测哪个项目。"
        ),
        parameters=UpdateJiraTicketParams,
    ),
    ToolDef(
        name="create_pr",
        description="提交一个 PR（替身）。Dev 自测通过后用它提 PR，并关联 Jira 工单号。",
        parameters=CreatePrParams,
    ),
]


async def _create_jira(title, description="", acceptance_criteria="", project="", context=None):
    gid = (context or {}).get("group_id")
    t = await get_jira().create_ticket(gid, title, description, acceptance_criteria, project)
    project_hint = f"（项目：{project}）" if project else ""
    return f"已创建 Jira 工单 {t['ticket_id']}：{title}{project_hint}"


async def _list_jira(context=None):
    gid = (context or {}).get("group_id")
    items = await get_jira().list_tickets(gid)
    if not items:
        return "当前没有 Jira 工单。"
    lines = []
    for t in items:
        project_tag = f" [项目:{t['project']}]" if t.get("project") else ""
        line = f"- {t['ticket_id']} [{t['status']}]{project_tag} {t['title']}"
        if t.get("acceptance_criteria"):
            line += f"\n    AC: {t['acceptance_criteria']}"
        lines.append(line)
    return "当前 Jira 工单：\n" + "\n".join(lines)


async def _update_jira(ticket_id, status, project=None, context=None):
    gid = (context or {}).get("group_id")
    try:
        result = await get_jira().update_ticket(gid, ticket_id, status, project=project)
    except ValueError as e:
        return f"[错误] {e}"
    from core.orchestration.rd_manager import rd_manager
    await rd_manager.render_board(gid)
    label = {"backlog": "待开发", "in_progress": "进行中", "done": "已完成"}.get(status, status)
    project_hint = f"，项目：{result['project']}" if result.get("project") else ""
    return f"工单 {result['ticket_id']} 状态已更新为【{label}】{project_hint}"


async def _create_pr(title, description="", ticket_ids=None, context=None):
    gid = (context or {}).get("group_id")
    pr = await get_git().create_pr(gid, title, description, ticket_ids)
    refs = ", ".join(pr["tickets"]) if pr["tickets"] else "(无)"
    return f"已提交 {pr['pr_id']}：{title}（{pr['url']}，关联工单 {refs}）"


def register_rd_tools() -> None:
    """把 RD 工具处理器注册进全局 tool_executor。"""
    handlers = {
        "create_jira_ticket": _create_jira,
        "list_jira_tickets": _list_jira,
        "update_jira_ticket": _update_jira,
        "create_pr": _create_pr,
    }
    for tdef in RD_TOOLS:
        tool_executor.register(tdef, handlers[tdef.name])
