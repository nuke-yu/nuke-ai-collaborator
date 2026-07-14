"""
core/orchestration/base.py — 编排层契约

编排层（Orchestrator）只做决策：给定状态 + 某一轮的产出，返回"接下来做什么"。
它不调用 AI、不发事件、不落 bot 消息、不碰 token —— 那些是执行层（Executor）和
胶水层（core/runner.py）的事。两层之间只通过下面这几个数据类过境。

向下（编排 → 执行）：WorkUnit  →  填进 ExecutionContext 的 bot/user_message/workflow_suffix
向上（执行 → 编排）：ExecutionResult.full_text  →  observe()
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class WorkUnit:
    """一个待执行的工作单元：让某个 bot 用某个 executor 跑一轮。"""
    bot: dict
    executor_id: str = "tool_loop_v1"      # 轴2：每个单元自己选执行插件
    trigger_msg: str = ""               # 触发语 → ExecutionContext.user_message
    prompt_suffix: str = ""             # 阶段指令 → ExecutionContext.workflow_suffix
    is_workflow: bool = False           # 由编排器创建的单元设为 True → ExecutionContext.is_workflow
    tag: dict = field(default_factory=dict)  # 编排私有记账（ticket 等），不进执行层


@dataclass
class SystemMessage:
    """编排层要广播的系统消息（"X 认领了 Y"）。由 runner 落库 + 广播。"""
    text: str
    sender_bot_id: int                  # DB 行挂在这个 bot 名下，前端显示为「工作流系统」


@dataclass
class OrchestratorStep:
    """编排决策的返回值。runner 负责把它"翻译"成副作用（发事件、跑单元）。"""
    next_units: list = field(default_factory=list)      # list[WorkUnit] —— runner 调度执行
    announcements: list = field(default_factory=list)   # list[SystemMessage] —— runner 广播
    broadcast_state: bool = False                       # runner 发 WorkflowUpdate(snapshot)
    done: bool = False                                  # 工作流结束
    confirm_gate: dict | None = None                    # 人确认门：runner 广播一张内联确认卡片并挂起，
                                                        # 等用户 confirm() 才推进。形如
                                                        # {gate_id, label, bot_id, stage_name}
    workflow_paused: object | None = None               # WorkflowPaused event to publish
                                                        # (e.g., completion_signal_missing)


class Orchestrator(ABC):
    """编排器契约。与 BotExecutor 同构，按 orchestrator_id 注册、可插拔。"""
    orchestrator_id: str = ""

    async def dispatch(self, group_id: int, message: dict, members: list, recent: list) -> OrchestratorStep:
        """Unified dispatch entry point for human messages during active workflow.
        
        By default, returns an empty OrchestratorStep (does not trigger any bots).
        Individual orchestrators can override this if human messages should trigger
        actions (e.g. BA stage in DeclarativeOrchestrator).
        """
        return OrchestratorStep()

    @abstractmethod
    def begin(self, group_id: int, spec) -> OrchestratorStep:
        """开始编排，返回第一批工作（首阶段可能由用户驱动，返回空 units）。"""

    @abstractmethod
    def observe(self, group_id: int, bot_id: int, response: str, signals: list[dict] | None = None) -> OrchestratorStep:
        """某个 bot 跑完一轮，更新内部状态并返回下一步。"""

    def confirm(self, group_id: int, gate_id: str | None = None,
                note: str = "", revise: bool = False) -> OrchestratorStep:
        """人在确认门点了「确认」或「修改」。若当前正挂在该门上则过门，否则空步。

        revise=True 携带 note：rework 门 → 打回目标阶段并附人工意见；完成门 → 不推进，
        打回当前阶段重做并附人工意见。默认无门语义，返回空步。"""
        return OrchestratorStep()

    def end(self, group_id: int) -> None:
        """结束编排，丢弃该 group 的内部状态。有状态的实现必须覆盖。"""

    def advance(self, group_id: int, prev_output: str = "") -> OrchestratorStep:
        """手动推进一步（API /next 用）。不支持手动推进的实现保持默认空步。"""
        return OrchestratorStep()

    def snapshot(self, group_id: int) -> dict:
        """给 WorkflowUpdate 用的状态快照。"""
        return {"active": False}

    def snapshot_state(self, state: dict | None) -> dict:
        """从持久化 blob 渲染前端快照（纯函数，不依赖内存 _state）——供主进程 REST 用：
        编排跑在 worker 进程，主进程内存恒空，只能从 group 私有库的 blob 渲染。
        默认仅报告活跃与否；有阶段视图的实现（如 declarative）应覆盖以给出完整快照。"""
        return {"active": bool(state)}

    # ── 当前在岗参与者（消费方 core.workflow / core.orchestrator 依赖） ──
    def current_bot(self, group_id: int) -> dict | None:
        """当前应当发言的单个 bot（无则 None，如池/并行阶段）。"""
        return None

    def current_pool_bots(self, group_id: int) -> list[int] | None:
        """当前在岗的一组 bot id（无池语义则 None）。"""
        return None

    def current_stage_role(self, group_id: int) -> str | None:
        """当前活跃阶段的角色标识（无阶段语义则为 None）。"""
        return None

    def current_thread_id(self, group_id: int) -> str | None:
        """当前讨论 topic 的作用域键（无 topic 语义的编排器恒为 None）。
        记忆按此键作用域读写摘要：None → 自由聊天，不强制注入历史摘要。"""
        return None

    def is_awaiting_confirm(self, group_id: int) -> bool:
        """当前工作流是否正处于等待人类确认的挂起状态。"""
        return False

    def system_suffix(self, group_id: int) -> str:
        """注入给当前阶段 bot 的系统提示后缀（无则空串）。"""
        return ""

    # ── 持久化 / 崩溃恢复（默认无能力，可选实现） ──
    def serialize(self, group_id: int) -> dict | None:
        """落盘用的完整内部状态（None = 无可持久化状态）。与 snapshot 不同：
        snapshot 是给前端看的精简视图，serialize 要能完整还原 self._state。"""
        return None

    def restore(self, group_id: int, state: dict) -> None:
        """重启时把 serialize 出来的状态装回内部（含必要的类型修复）。"""

    def resume_units(self, group_id: int) -> list:
        """恢复后需要重新派发的在飞工作单元（list[WorkUnit]）。"""
        return []

    def start_time(self, group_id: int) -> str | None:
        """工作流启动的 UTC 时间字符串，格式 'YYYY-MM-DD HH:MM:SS' (或 None)"""
        return None

    def parse_spec(self, body: dict, all_bots: dict[int, dict]) -> dict:
        """从前端 payload 的 body 中解析并还原出适合本编排器的 spec。"""
        return body.get("spec", {})

    def get_viewpoints_cache(self, group_id: int) -> dict | None:
        """返回语义摘要压缩缓存字典（就地修改即持久化）。
        None 表示该编排器不支持语义压缩，runner 跳过压缩逻辑。"""
        return None

    def participant_count(self, group_id: int) -> int | None:
        """返回当前工作流的参与 Bot 数，用于压缩窗口计算。None = 未知。"""
        return None

    def info(self) -> dict:
        return {"orchestrator_id": self.orchestrator_id}
