import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}})
    concurrency_safe: bool = False  # True = read-only, safe to run in parallel with other safe tools


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
            "tools": [{"name": t.name, "description": t.description, "concurrency_safe": t.concurrency_safe} for t in self.tools],
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


class InteractionAdapter(ABC):
    """
    Interface for side-effect dispatching (Point 3: Decoupling).
    Abstracts DB storage, UI broadcasting, and session event logging.
    """
    @abstractmethod
    async def create_session(self, **kwargs):
        """Create a new agent execution session."""
        pass

    @abstractmethod
    async def update_session_status(self, session_id: str, status: str):
        """Update the status of a session (e.g., 'running', 'completed')."""
        pass

    @abstractmethod
    async def broadcast(self, group_id: int, payload: dict):
        """Broadcast a message to the UI (WebSocket)."""
        pass

    @abstractmethod
    async def save_message(self, group_id: int, member_id: int, content: str, **kwargs) -> int:
        """Save a message to the chat history database."""
        pass

    @abstractmethod
    async def append_session_event(self, session_id: str, event_type: str, payload: dict):
        """Log an audit event for the current execution session."""
        pass

    @abstractmethod
    async def save_session_snapshot(self, session_id: str, messages: list):
        """Persist a full context snapshot for crash recovery."""
        pass

    @abstractmethod
    async def update_session_tokens(self, session_id: str, **usage):
        """Add token usage metrics to the session."""
        pass


@dataclass
class ExecutionContext:
    bot: dict
    group_id: int
    user_message: str
    sender: dict
    history: list[dict]
    all_bots: list[dict]
    all_members: list[dict]
    broadcaster: Any          # (DEPRECATED: Use interaction.broadcast instead)
    interaction: InteractionAdapter = None  # Point 3: Side-effect dispatcher
    active_ticket_id: str | None = None      # Point 4: Current Jira ticket being worked on
    workflow_suffix: str = ""
    group_name: str = ""
    group_announcement: str = ""
    steer_channel: asyncio.Queue | None = None
    spawn_depth: int = 0
    ruleset: Any = None       # permissions.Ruleset | None; None = no permission checking
    file_url: str | None = None
    file_type: str | None = None
    # Crash recovery (DFT-018): when set, the executor continues this existing
    # session from its reconstructed WAL messages instead of starting fresh.
    resume_session_id: str | None = None
    resume_messages: list | None = None


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
