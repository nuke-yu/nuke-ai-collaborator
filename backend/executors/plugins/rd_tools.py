"""executors/plugins/rd_tools.py — RD 流水线工具（Jira / PR 替身）。

给 BA/Dev 在工作流里调用的结构化工具：
  - create_jira_ticket：BA 确认需求后建工单（含 AC）。
  - list_jira_tickets：Dev/QA 查看要做/要测的工单（只读）。
  - create_pr：Dev 自测后提 PR（替身），关联 Jira 工单。

工具产出落到 integrations 的本地替身(tickets 表 / 工作区文件)，真 Jira/Git 接入时
换 integrations 实现即可，这里不变。这些是内部记账型工具，人把关在工作流的 4 道门，
不在每次工具调用——故在 workspace_tools 的权限钩子里 auto-allow（见 _AUTO_ALLOW_TOOLS）。
"""
from executors.base import ToolDef
from executors import tool_executor
from integrations.jira import get_jira
from integrations.git import get_git

RD_TOOLS = [
    ToolDef(
        name="create_jira_ticket",
        description="创建一个 Jira 工单（含验收标准 AC）。BA 在需求确认后用它建工单。",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "工单标题"},
                "description": {"type": "string", "description": "工单描述/范围"},
                "acceptance_criteria": {"type": "string", "description": "验收标准(AC)，可多条"},
            },
            "required": ["title"],
        },
    ),
    ToolDef(
        name="list_jira_tickets",
        description="列出本群当前所有 Jira 工单（标题/状态/AC）。Dev/QA 用它查看要做/要测什么。",
        parameters={"type": "object", "properties": {}},
        concurrency_safe=True,
    ),
    ToolDef(
        name="create_pr",
        description="提交一个 PR（替身）。Dev 自测通过后用它提 PR，并关联 Jira 工单号。",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "PR 标题"},
                "description": {"type": "string", "description": "PR 描述/改动说明"},
                "ticket_ids": {
                    "type": "array", "items": {"type": "string"},
                    "description": "关联的 Jira 工单号，如 ['DFT-1','DFT-2']",
                },
            },
            "required": ["title"],
        },
    ),
]


async def _create_jira(title, description="", acceptance_criteria="", context=None):
    gid = (context or {}).get("group_id")
    t = await get_jira().create_ticket(gid, title, description, acceptance_criteria)
    return f"已创建 Jira 工单 {t['ticket_id']}：{title}"


async def _list_jira(context=None):
    gid = (context or {}).get("group_id")
    items = await get_jira().list_tickets(gid)
    if not items:
        return "当前没有 Jira 工单。"
    lines = []
    for t in items:
        line = f"- {t['ticket_id']} [{t['status']}] {t['title']}"
        if t.get("acceptance_criteria"):
            line += f"\n    AC: {t['acceptance_criteria']}"
        lines.append(line)
    return "当前 Jira 工单：\n" + "\n".join(lines)


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
        "create_pr": _create_pr,
    }
    for tdef in RD_TOOLS:
        tool_executor.register(tdef, handlers[tdef.name])
