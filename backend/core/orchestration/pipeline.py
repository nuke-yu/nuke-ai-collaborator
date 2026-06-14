"""core/orchestration/pipeline.py — RD 人确认流水线规格（MVP1）

把「人把关的 BA→Dev→QA 流水线」表达成一串带 gate 的 single 阶段：

    BA(澄清+建Jira) ─门1─► Dev开发 ─门2─► QA测试 ─门3─► 完成

每个阶段都是数据：本阶段任务指令(instruction)、完成信号(done_keyword)、
人确认门标签(gate_label)。bot 吐出完成信号后不自动推进，而是挂起等人点「确认」
（门机制见 stages.SingleStage / declarative._raise_gate）。

注意：
- 完成信号优先走结构化工具调用：阶段完成调 signal_stage_done、返工调 signal_rework，
  observe() 读 tool-call 结果做状态推进（确定性、可审计，不会被人格化 bot 改写）。
  done_keyword/fail_keyword（[[BA_DONE]] / [[DEV_DONE]] / [[QA_DONE]] / [[QA_FAIL]] 等
  哨兵）保留为纯代码层 fallback：模型若没调工具而是吐了哨兵文本，stages._signal_in 仍
  能兜住（大小写/空格/全角括号容错）。提示词只驱动工具，不再要求模型输出哨兵。
- 澄清需求、确认、拆 Jira 工单都是 BA 一个人的活，合并成单一 BA 阶段（不再拆两段）。
- 首阶段(BA)由用户驱动：begin 不自动派发，用户开口聊需求即进入；本阶段任务指令
  (instruction) 不经 enter 的 trigger 送达，而是随 system_suffix 一起带给 BA
  （见 declarative.system_suffix 对 idx==0 的处理）。后续阶段靠 enter 的 instruction
  开场（交棒即开工）。
- 建 Jira / 提 PR 当前是替身（用文字产出），真 Jira/Git 工具在 step 3 接上。
"""

PIPELINE_ID = "rd_gate_v1"


# BA 阶段只澄清需求 + 列工单，不写代码。用工具白名单在工具层物理拿掉一切写/执行
# 类工具（write_file / write_local_file / run_shell / create_pr / spawn_agent），
# 只留读取、技能、建/查工单——光靠提示约束挡不住模型在需求阶段直接动手写网页。
# 例外：放行 signal_stage_done（阶段完成的结构化控制信号），否则 BA 阶段连确认门都
# 无法用工具驱动，只能退回脆弱的哨兵文本匹配（R1 通电的关键一环）。
_BA_ALLOWED_TOOLS = [
    "read_file", "read_local_file", "list_workspace",
    "run_skill", "create_jira_ticket", "list_jira_tickets",
    "signal_stage_done",
]


def _gated_stage(bot: dict, *, instruction: str, done_keyword: str, gate_label: str,
                 allowed_tools: list[str] | None = None,
                 fail_keyword: str | None = None, rework_to: int | None = None,
                 fail_gate_label: str | None = None) -> dict:
    stage = {
        **bot,
        "stage_type": "single",
        "gate": True,
        "instruction": instruction,
        "done_keyword": done_keyword,
        "gate_label": gate_label,
    }
    if allowed_tools is not None:
        stage["allowed_tools"] = allowed_tools
    # 返工：本阶段没通过时输出 fail_keyword，确认后回退到 rework_to 指向的阶段重做。
    if fail_keyword is not None:
        stage["fail_keyword"] = fail_keyword
        stage["rework_to"] = rework_to
        stage["fail_gate_label"] = fail_gate_label
    return stage


def build_rd_pipeline(ba: dict, dev: dict, qa: dict) -> list[dict]:
    """用三个角色 bot 拼出 3 道门的 RD 流水线 ordered_stages。"""
    return [
        _gated_stage(
            ba,
            instruction=(
                "和用户多轮对话，把需求问清楚：目标、范围、边界、验收标准。"
                "充分澄清并与用户对齐后，把需求拆成 Jira 工单，每个工单包含："
                "标题、描述、验收标准(AC)。（当前 Jira 为替身，先用清晰的文字列出工单清单。）"
                "【本阶段铁律】你是需求分析，不是开发：绝对不要写任何代码或网页文件，"
                "不要创建项目目录、不要落盘任何实现。开发是确认通过后下一阶段 Dev 的活。"
                "（系统也已在工具层禁用了你的 write_file / run_shell 等写入能力。）"
                "需求总结和工单清单都给完后，向用户说明『都就绪了，是否让开发开始？』，"
                "并调用工具 signal_stage_done(reason=\"需求已澄清、工单已就绪的简短说明\") "
                "通知系统——系统识别到这个工具调用才会给用户弹出确认卡片。"
                "没完全澄清、工单没列完之前，绝对不要调用它。"
            ),
            done_keyword="[[BA_DONE]]",
            gate_label="确认需求已整理清楚、Jira 工单已建好",
            allowed_tools=_BA_ALLOWED_TOOLS,
        ),
        _gated_stage(
            dev,
            instruction=(
                "按上面的 Jira 工单开发。"
                "【编辑与开发规范】\n"
                "- 修改已有文件绝对优先使用 edit_file（只发 diff，避免大文件被单次输出长度截断）；同一文件多处改动用 edits 数组一次提交；\n"
                "- write_file 仅用于新建文件或整文件重写；\n"
                "- 严禁把完整源码贴进聊天回复，只需说明实现方案与落盘文件。\n"
                "开发完成后做代码自测，并提供 PR 描述（当前 Git 为替身）。"
                "确认代码落盘且自测通过后，调用工具 signal_stage_done(reason=\"已落盘文件清单与自测结论的简短说明\") 通知系统；没落盘前绝对不要调用它。"
            ),
            done_keyword="[[DEV_DONE]]",
            gate_label="确认开发完成、PR 已提",
        ),
        _gated_stage(
            qa,
            instruction=(
                "按 Jira 工单的验收标准(AC)做冒烟测试：逐条给出通过/不通过及理由，"
                "最后给出测试结论。"
                "【收尾规则】只有当所有 AC 全部通过时，才调用工具 "
                "signal_stage_done(reason=\"各 AC 通过情况与测试结论\") 通知系统。"
                "只要有任何一条 AC 不通过，就绝对不要调用 signal_stage_done，"
                "而是调用工具 signal_rework(target_stage=\"Dev\", reason=\"未通过的 AC 与 bug 说明\")——"
                "系统会弹确认卡片，人确认后把问题打回 Dev 修复，修好会再回到你这里重测。"
                "两个工具互斥，一次只调用其中一个；测试还没做完之前两个都不要调用。"
            ),
            done_keyword="[[QA_DONE]]",
            gate_label="确认测试通过",
            fail_keyword="[[QA_FAIL]]",
            rework_to=1,  # 打回 Dev（流水线第 2 个阶段，下标 1）
            fail_gate_label="QA 未通过：是否打回 Dev 修复并重测？",
        ),
    ]
