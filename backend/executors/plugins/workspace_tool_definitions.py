"""Stable ToolDef registry for workspace tools."""
from __future__ import annotations

from executors.base import ToolDef
from executors.plugins.workspace_tool_models import (
    ReadFileParams, SliceReadParams, RunCodeParams, WriteFileParams,
    EditFileParams, ReadAnchoredParams, EditAnchoredParams, ListWorkspaceParams,
    RunSkillParams, RunShellParams, ReadLocalFileParams, WriteLocalFileParams,
    SpawnAgentParams, SignalStageDoneParams, SignalReworkParams,
)

WORKSPACE_TOOLS = [
    ToolDef(name="read_file", description="读取 Bot 工作区内的文件内容", parameters=ReadFileParams, concurrency_safe=True),
    ToolDef(name="slice_read", description="按 spill locator 读取超长工具输出的有限行范围", parameters=SliceReadParams, concurrency_safe=True),
    ToolDef(name="run_code", description="在受限本地 Code Mode 中批量执行 workspace SDK 脚本；禁止 import、shell、网络和任意文件访问", parameters=RunCodeParams),
    ToolDef(name="write_file", description="仅用于新建文件或整文件重写；改已有文件请用 edit_file（只发 diff，避免大文件被输出长度截断）。", parameters=WriteFileParams),
    ToolDef(name="edit_file", description=("对工作区已有文件做精确字符串替换（只发改动片段，不必重发整文件）。把 old_string 替换为 new_string；old_string 必须在文件中唯一（否则报错，请加更多上下文或用 replace_all）。修改已有文件首选本工具。一次改多处可用 edits 数组（顺序应用、原子、一次提交）。"), parameters=EditFileParams),
    ToolDef(name="read_anchored", description=("读取文件并给每行打行哈希锚（L<行号>#<hash>）。配合 edit_anchored 按锚精准改单行/少数行——锚用内容哈希定位，行位移也有效。大文件里改个别行优于重抄整段 old_string。"), parameters=ReadAnchoredParams, concurrency_safe=True),
    ToolDef(name="edit_anchored", description=("按行哈希锚编辑文件（先用 read_anchored 取锚）。edits 顺序应用、原子（任一锚失效/冲突则整体不落盘）。每项 {anchor, op, text}，op ∈ replace/delete/insert_after。"), parameters=EditAnchoredParams),
    ToolDef(name="list_workspace", description="列出 Bot 工作区的目录结构", parameters=ListWorkspaceParams, concurrency_safe=True),
    ToolDef(name="run_skill", description="执行 skills/ 目录中的技能脚本", parameters=RunSkillParams),
    ToolDef(name="run_shell", description="在本地执行 shell 命令，返回 stdout / stderr / exit_code", parameters=RunShellParams),
    ToolDef(name="read_local_file", description="按绝对路径读取当前 Bot 私有区、群组共享区或已调用技能的附件文件", parameters=ReadLocalFileParams, concurrency_safe=True),
    ToolDef(name="write_local_file", description="按绝对路径写入当前 Bot 私有区或群组共享区（自动创建父目录）", parameters=WriteLocalFileParams),
    ToolDef(name="spawn_agent", description="派生子 Agent：将子任务委托给另一个 Bot 执行。background=true 时立即返回不等待结果，子 Agent 在后台运行，完成后结果自动注回当前对话", parameters=SpawnAgentParams),
    ToolDef(name="signal_stage_done", description="当完成当前阶段的任务时，调用此工具以通知系统阶段已完成，并触发进入下一阶段（门）。", parameters=SignalStageDoneParams, concurrency_safe=True),
    ToolDef(name="signal_rework", description="当发现上游阶段的问题需要打回重做（返工）时，调用此工具以将工作流回退到指定阶段。", parameters=SignalReworkParams, concurrency_safe=True),
]
