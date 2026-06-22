import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable


from pydantic import BaseModel

@dataclass
class ToolDef:
    name: str
    description: str
    _parameters: Any = field(default=None, repr=False)
    concurrency_safe: bool = False  # True = read-only, safe to run in parallel with other safe tools
    # Optional: bind the handler directly to the definition so we don't need
    # two parallel dicts (_defs / _handlers) in tool_executor.  Stays None for
    # ToolDefs that are created declaratively (e.g. in PluginManifest serialisation).
    handler: Callable | None = field(default=None, repr=False, compare=False)

    def __init__(self, name: str, description: str, parameters: Any = None, concurrency_safe: bool = False, handler: Callable | None = None):
        self.name = name
        self.description = description
        self._parameters = parameters
        self.concurrency_safe = concurrency_safe
        self.handler = handler

    @property
    def parameters(self) -> dict:
        if self._parameters is None:
            return {"type": "object", "properties": {}}
        if isinstance(self._parameters, dict):
            return self._parameters
        if isinstance(self._parameters, type) and issubclass(self._parameters, BaseModel):
            schema = self._parameters.model_json_schema()
            schema = dict(schema)
            schema.pop("title", None)
            if "properties" in schema:
                new_props = {}
                for k, v in schema["properties"].items():
                    if isinstance(v, dict):
                        v = dict(v)
                        v.pop("title", None)
                    new_props[k] = v
                schema["properties"] = new_props
            return schema
        return self._parameters



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
    interaction: InteractionAdapter = None  # Point 3: Side-effect dispatcher
    active_ticket_id: str | None = None      # Point 4: Current Jira ticket being worked on
    workflow_suffix: str = ""
    is_workflow: bool = False
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
    signals: list[dict] = None


class ToolProvider(ABC):
    """
    Abstract base for all tool-execution providers.

    Each concrete provider is responsible for a single *kind* of tool
    (builtin Python handlers, Skill-markdown expansions, MCP remote calls …).
    The ToolRouter queries ``can_handle`` to route an incoming tool call to the
    correct provider without any provider needing to know about the others.
    """

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Stable identifier used in logs and metrics (e.g. 'builtin', 'skill', 'mcp')."""

    @abstractmethod
    def discover_tools(self) -> list["ToolDef"]:
        """
        Return all tool definitions this provider currently exposes.

        Called by ToolRouter.get_all_schemas() to assemble the schema list sent
        to the LLM.  Builtin providers return a static list synchronously.
        A future McpClientToolProvider that needs async discovery should cache
        the result after an initial async init and return it here synchronously.
        """

    @abstractmethod
    def can_handle(self, name: str) -> bool:
        """
        Return True if this provider should execute the tool named *name*.

        The router iterates registered providers in insertion order and hands
        off to the first one that returns True.  Providers based on a namespace
        prefix (e.g. ``mcp::``) should check for that prefix; providers for a
        fixed set of names should do a membership test.
        """

    @abstractmethod
    async def execute(self, name: str, arguments: dict, context: dict) -> tuple[str, bool]:
        """
        Execute the tool and return ``(result_text, is_error)``.

        *arguments* has already been alias-normalized by the router before this
        call; providers do not need to repeat the normalization.
        is_error=True signals the router to treat the result as a failure.
        """


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


GROUP_I18N = {
    "zh": {
        "group": "群组：",
        "announcement": "公告：",
        "human_members": "人类成员：",
        "ai_members": "AI 成员：",
        "role_format": "{name}（{role}）"
    },
    "en": {
        "group": "Group: ",
        "announcement": "Announcement: ",
        "human_members": "Human Members: ",
        "ai_members": "AI Members: ",
        "role_format": "{name} ({role})"
    }
}


def build_group_section(ctx: "ExecutionContext") -> str:
    """Build a group context string for injection into system prompt."""
    from workspace.layout import get_group_language
    lang = get_group_language(ctx.group_id)
    L = GROUP_I18N.get(lang, GROUP_I18N["zh"])

    lines = []
    if ctx.group_name:
        lines.append(f"{L['group']}{ctx.group_name}")
    if ctx.group_announcement:
        lines.append(f"{L['announcement']}{ctx.group_announcement}")
    members = ctx.all_members
    if members:
        humans = [m["name"] for m in members if m["type"] == "human"]
        bots = [
            L["role_format"].format(name=m["name"], role=m.get("role") or "Bot")
            for m in members if m["type"] == "bot"
        ]
        if humans:
            lines.append(f"{L['human_members']}{', '.join(humans)}")
        if bots:
            lines.append(f"{L['ai_members']}{', '.join(bots)}")
    return "\n".join(lines)

