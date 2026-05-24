from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}})


@dataclass
class WorkspaceConfig:
    startup_files: list[str] = field(default_factory=list)
    skill_discovery: bool = False
    writeback_pattern: str | None = None  # e.g. "logs/{date}.md"


@dataclass
class CollabConfig:
    can_handoff: bool = True
    can_spawn_subagent: bool = False


@dataclass
class PluginManifest:
    description: str = ""
    tools: list[ToolDef] = field(default_factory=list)
    memory_layers: list[str] = field(default_factory=lambda: ["short_term"])
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    collaboration: CollabConfig = field(default_factory=CollabConfig)
    max_iterations: int = 1

    def to_dict(self) -> dict:
        return {
            "description": self.description,
            "tools": [{"name": t.name, "description": t.description} for t in self.tools],
            "memory_layers": self.memory_layers,
            "workspace": {
                "startup_files": self.workspace.startup_files,
                "skill_discovery": self.workspace.skill_discovery,
                "writeback_pattern": self.workspace.writeback_pattern,
            },
            "collaboration": {
                "can_handoff": self.collaboration.can_handoff,
                "can_spawn_subagent": self.collaboration.can_spawn_subagent,
            },
            "max_iterations": self.max_iterations,
        }


@dataclass
class ExecutionContext:
    bot: dict
    group_id: int
    user_message: str
    sender: dict
    history: list[dict]
    all_bots: list[dict]
    all_members: list[dict]
    broadcaster: Any          # ws_manager.ConnectionManager
    workflow_suffix: str = ""
    group_name: str = ""
    group_announcement: str = ""


@dataclass
class ExecutionResult:
    full_text: str
    msg_id: int | None


class BotExecutor(ABC):
    executor_id: str = ""
    display_name: str = ""
    manifest: PluginManifest = field(default_factory=PluginManifest)

    @abstractmethod
    async def run(self, ctx: ExecutionContext) -> ExecutionResult:
        pass

    def register_tools(self):
        """Override to register this plugin's tools into tool_executor. Called on load."""
        pass

    def info(self) -> dict:  # noqa: keep at bottom
        return {
            "executor_id": self.executor_id,
            "display_name": self.display_name,
            "manifest": self.manifest.to_dict(),
        }


def build_group_section(ctx: "ExecutionContext") -> str:
    """Build a group context string for injection into system prompt."""
    lines = []
    if ctx.group_name:
        lines.append(f"群组：{ctx.group_name}")
    if ctx.group_announcement:
        lines.append(f"公告：{ctx.group_announcement}")
    members = ctx.all_members
    if members:
        humans = [m["name"] for m in members if m["type"] == "human"]
        bots = [f"{m['name']}（{m.get('role') or 'Bot'}）" for m in members if m["type"] == "bot"]
        if humans:
            lines.append(f"人类成员：{', '.join(humans)}")
        if bots:
            lines.append(f"AI 成员：{', '.join(bots)}")
    return "\n".join(lines)
