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
    executor_id: str = "simple_v1"      # 轴2：每个单元自己选执行插件
    trigger_msg: str = ""               # 触发语 → ExecutionContext.user_message
    prompt_suffix: str = ""             # 阶段指令 → ExecutionContext.workflow_suffix
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


class Orchestrator(ABC):
    """编排器契约。与 BotExecutor 同构，按 orchestrator_id 注册、可插拔。"""
    orchestrator_id: str = ""

    @abstractmethod
    def begin(self, group_id: int, spec) -> OrchestratorStep:
        """开始编排，返回第一批工作（首阶段可能由用户驱动，返回空 units）。"""

    @abstractmethod
    def observe(self, group_id: int, bot_id: int, response: str) -> OrchestratorStep:
        """某个 bot 跑完一轮，更新内部状态并返回下一步。"""

    def snapshot(self, group_id: int) -> dict:
        """给 WorkflowUpdate 用的状态快照。"""
        return {"active": False}

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

    def info(self) -> dict:
        return {"orchestrator_id": self.orchestrator_id}
