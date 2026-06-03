"""core/orchestration/pipeline.py — RD 人确认流水线规格（MVP1）

把「人把关的 BA→Dev→QA 流水线」表达成一串带 gate 的 single 阶段：

    BA澄清 ─门1─► BA建Jira ─门2─► Dev开发 ─门3─► QA测试 ─门4─► 完成

每个阶段都是数据：本阶段任务指令(instruction)、完成关键词(done_keyword)、
人确认门标签(gate_label)。bot 说出关键词后不自动推进，而是挂起等人点「确认」
（门机制见 stages.SingleStage / declarative._raise_gate）。

注意：
- BA 出现两次（先澄清需求、再建 Jira），是同一个 bot 的两个连续阶段。
- 首阶段(BA澄清)由用户驱动：begin 不自动派发，用户开口聊需求即进入；BA 靠
  system_suffix 知道「完成时说『需求确认完成』」。后续阶段靠 enter 的 instruction
  开场（交棒即开工）。
- 建 Jira / 提 PR 当前是替身（用文字产出），真 Jira/Git 工具在 step 3 接上。
"""

PIPELINE_ID = "rd_gate_v1"


def _gated_stage(bot: dict, *, instruction: str, done_keyword: str, gate_label: str) -> dict:
    return {
        **bot,
        "stage_type": "single",
        "gate": True,
        "instruction": instruction,
        "done_keyword": done_keyword,
        "gate_label": gate_label,
    }


def build_rd_pipeline(ba: dict, dev: dict, qa: dict) -> list[dict]:
    """用三个角色 bot 拼出 4 道门的 RD 流水线 ordered_stages。"""
    return [
        _gated_stage(
            ba,
            instruction=(
                "和用户多轮对话，把需求问清楚：目标、范围、边界、验收标准。"
                "充分澄清并与用户对齐后，给出一份结构化的需求总结，"
                "然后在回复末尾单独说「需求确认完成」。没完全澄清之前不要说这句话。"
            ),
            done_keyword="需求确认完成",
            gate_label="确认需求已整理清楚",
        ),
        _gated_stage(
            ba,
            instruction=(
                "把刚刚确认的需求拆成 Jira 工单，每个工单包含：标题、描述、验收标准(AC)。"
                "（当前 Jira 为替身，先用清晰的文字列出工单清单。）"
                "全部列完后在末尾说「JIRA创建完成」。"
            ),
            done_keyword="JIRA创建完成",
            gate_label="确认 Jira 工单已建好",
        ),
        _gated_stage(
            dev,
            instruction=(
                "按上面的 Jira 工单开发：说明实现方案与关键代码思路，做代码自测，"
                "并给出 PR 描述（当前 Git 为替身，用文字描述 PR）。完成后说「开发完成」。"
            ),
            done_keyword="开发完成",
            gate_label="确认开发完成、PR 已提",
        ),
        _gated_stage(
            qa,
            instruction=(
                "按 Jira 工单的验收标准(AC)做冒烟测试：逐条给出通过/不通过及理由，"
                "最后给出测试结论。完成后说「测试完成」。"
            ),
            done_keyword="测试完成",
            gate_label="确认测试通过",
        ),
    ]
