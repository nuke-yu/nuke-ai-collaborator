"""core/orchestration/pipeline.py — RD 人确认流水线规格（MVP1）

把「人把关的 BA→Dev→QA 流水线」表达成一串带 gate 的 single 阶段：

    BA(澄清+建Jira) ─门1─► Dev开发 ─门2─► QA测试 ─门3─► 完成

每个阶段都是数据：本阶段任务指令(instruction)、完成信号(done_keyword)、
人确认门标签(gate_label)。bot 吐出完成信号后不自动推进，而是挂起等人点「确认」
（门机制见 stages.SingleStage / declarative._raise_gate）。

注意：
- 完成信号用「哨兵标记」（[[BA_DONE]] / [[DEV_DONE]] / [[QA_DONE]]）而不是中文短语：
  人格化 bot 几乎必然把长中文句子改写掉，匹配就失败、确认卡片永远弹不出来；带方括号
  的怪 token 模型倾向一字不差照抄，且 stages._signal_in 做了大小写/空格/全角括号容错。
- 澄清需求、确认、拆 Jira 工单都是 BA 一个人的活，合并成单一 BA 阶段（不再拆两段）。
- 首阶段(BA)由用户驱动：begin 不自动派发，用户开口聊需求即进入；本阶段任务指令
  (instruction) 不经 enter 的 trigger 送达，而是随 system_suffix 一起带给 BA
  （见 declarative.system_suffix 对 idx==0 的处理）。后续阶段靠 enter 的 instruction
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
    """用三个角色 bot 拼出 3 道门的 RD 流水线 ordered_stages。"""
    return [
        _gated_stage(
            ba,
            instruction=(
                "和用户多轮对话，把需求问清楚：目标、范围、边界、验收标准。"
                "充分澄清并与用户对齐后，把需求拆成 Jira 工单，每个工单包含："
                "标题、描述、验收标准(AC)。（当前 Jira 为替身，先用清晰的文字列出工单清单。）"
                "需求总结和工单清单都给完后，向用户说明『都就绪了，是否让开发开始？』，"
                "并在回复的最后一行单独、原样输出标记 [[BA_DONE]]（一字不差，"
                "不要翻译/改写/加别的字）——系统识别到它才会给用户弹出确认卡片。"
                "没完全澄清、工单没列完之前，绝对不要输出这个标记。"
            ),
            done_keyword="[[BA_DONE]]",
            gate_label="确认需求已整理清楚、Jira 工单已建好",
        ),
        _gated_stage(
            dev,
            instruction=(
                "按上面的 Jira 工单开发。"
                "【硬性要求】默认先在自己的工作区（Workspace）中新建一个目录（如以项目或工单命名的文件夹，例如 calculator/ ），"
                "然后必须用 write_file 工具将完整代码真正写到该新建目录下（如 calculator/index.html ），一个文件一次 write_file 调用；"
                "**严禁把完整源码贴进聊天回复**——聊天里只用简短文字说明：实现方案、"
                "关键设计决策、以及你写了哪些文件。写完后做代码自测，并用文字给出 PR 描述"
                "（当前 Git 为替身）。确认代码已落盘且自测通过后，在回复的最后一行"
                "单独、原样输出标记 [[DEV_DONE]]（一字不差，不要改写）；没真正写出文件之前，"
                "绝对不要输出它。"
            ),
            done_keyword="[[DEV_DONE]]",
            gate_label="确认开发完成、PR 已提",
        ),
        _gated_stage(
            qa,
            instruction=(
                "按 Jira 工单的验收标准(AC)做冒烟测试：逐条给出通过/不通过及理由，"
                "最后给出测试结论。完成后在回复的最后一行单独、原样输出标记 [[QA_DONE]]"
                "（一字不差，不要改写）。完成之前不要输出它。"
            ),
            done_keyword="[[QA_DONE]]",
            gate_label="确认测试通过",
        ),
    ]
