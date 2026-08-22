"""Pydantic request models for workspace tools."""
from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field


class ReadFileParams(BaseModel):
    path: str = Field(..., description="相对于工作区根目录的路径")
    offset: Optional[int] = Field(None, description="读取文件的起始字符偏移量")
    limit: Optional[int] = Field(None, description="最大读取字符长度")

class SliceReadParams(BaseModel):
    locator: str = Field(..., description="超长工具输出返回的 spill:// 句柄")
    start_line: int = Field(..., description="起始行，1-based，包含")
    end_line: int = Field(..., description="结束行，1-based，包含；单次最多 200 行")

class RunCodeParams(BaseModel):
    code: str = Field(..., description="使用 tools.read/write/grep SDK 的受限 Python 脚本")

class WriteFileParams(BaseModel):
    path: str
    content: str

class SingleEdit(BaseModel):
    old_string: str
    new_string: str

class EditFileParams(BaseModel):
    path: str = Field(..., description="相对于工作区根目录的路径")
    old_string: Optional[str] = Field(None, description="单次替换：要被替换的原文（需与文件内容一致，可含多行）")
    new_string: Optional[str] = Field(None, description="单次替换：替换后的新内容")
    replace_all: bool = Field(False, description="是否替换所有匹配，默认 false")
    edits: Optional[List[SingleEdit]] = Field(None, description="批量替换：多处一次提交，顺序应用、原子（任一未命中则整体不落盘）。与 old_string/new_string 二选一。")

class ReadAnchoredParams(BaseModel):
    path: str = Field(..., description="相对于工作区根目录的路径")

class AnchoredEditItem(BaseModel):
    anchor: str = Field(..., description="read_anchored 给出的锚，如 L12#a3f0c1d")
    op: Optional[str] = Field(None, description="replace（默认）/ delete / insert_after")
    text: Optional[str] = Field(None, description="replace/insert_after 的新文本；delete 可省")

class EditAnchoredParams(BaseModel):
    path: str = Field(..., description="相对于工作区根目录的路径")
    edits: List[AnchoredEditItem] = Field(..., description="锚点编辑列表")

class ListWorkspaceParams(BaseModel):
    pass

class RunSkillParams(BaseModel):
    name: str = Field(..., description="技能文件名（不含扩展名）")
    args: str = Field("", description="运行技能脚本的参数，默认为空字串")

class RunShellParams(BaseModel):
    cmd: str = Field(..., description="要执行的 shell 命令")
    cwd: Optional[str] = Field(None, description="工作目录：默认=群组共享工作区根（相对路径如 workspace/<repo> 落共享区）；私有区用 skills/ 或 logs/ 前缀；也可传绝对路径（须在本群组工作区内）")
    timeout: int = Field(30, description="超时秒数，默认 30")
    background: bool = Field(False, description="后台运行，立即返回 PID")

class ReadLocalFileParams(BaseModel):
    path: str = Field(..., description="文件的绝对路径")

class WriteLocalFileParams(BaseModel):
    path: str = Field(..., description="文件的绝对路径")
    content: str

class SpawnAgentParams(BaseModel):
    bot_name: str = Field(..., description="目标 Bot 的名称")
    task: str = Field(..., description="委托给子 Agent 的具体任务描述")
    background: bool = Field(False, description="后台运行，立即返回不等待结果")

class SignalStageDoneParams(BaseModel):
    reason: str = Field(..., description="完成当前阶段工作的简短理由、最终结论或交付物说明")

class SignalReworkParams(BaseModel):
    target_stage: str = Field(..., description="需要返工回到的目标阶段名称或角色（如 'Dev'、'QA' 等）")
    reason: str = Field(..., description="需要返工的理由、Bug 报告或测试未通过的说明")
    rework_to_idx: Optional[int] = Field(None, description="（可选）需要返工回到的目标阶段的 0-based 索引")
