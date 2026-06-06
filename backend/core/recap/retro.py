import json
import logging
import re
from pathlib import Path
import db
from db import get_db, write_connect, get_members, get_messages, global_db
from workspace import group_workspace
from ai.client import call_ai_once
from bus import bus

log = logging.getLogger(__name__)

# Concurrency lock
_generating_retros = set()

_RETRO_SYSTEM_PROMPT = """\
你是一个项目协作助手。请根据提供的最近开发对话与执行历史，生成一份结构化的【项目复盘与技术债清单】。

复盘生成原则（强去噪规则）：
1. 绝对不要包含任何日常问候、寒暄（如“你好”、“收到”、“辛苦了”等）。
2. 绝对不要记录没有架构影响的正常工具执行过程（如正常的 git commit/push、ls 列目录、无错编译、无错测试运行等）。
3. 过滤掉重复语法修正或代码格式化等琐碎细节。
4. 如果有失败的尝试或踩坑经历，必须重点提炼其原因和解决方案（这对于防止未来重蹈覆辙至关重要）。

请生成以下 4 个部分，使用 Markdown 格式输出：

## 1. 最终成果摘要 (Executive Summary)
- 概括本轮开发最终达成的目标和交付成果（1-2句）。

## 2. 核心技术决策与动因 (Key Decisions & Rationale)
- 记录了哪些核心文件的修改或新增，为什么采用这种设计模式或API调用？

## 3. 避坑指南与教训 (Lessons & Anti-Patterns)
- 记录在开发过程中尝试过但失败的方案、抛出的关键报错，以及最终是如何解决的。

## 4. 技术债与后续待办 (Tech Debt & Next Steps)
- 记录留下的临时规避方案、未解决的潜在隐患，以及后续重构或开发的具体建议。
"""


async def generate_ticket_retrospective(group_id: int) -> str | None:
    """
    Workflow run-level retrospective generator.
    Queries new messages since the last saved anchor ID, calls the specialized LLM denoising prompt,
    saves the markdown run report, updates RETRO_LATEST.md, and caches the summary in groups.away_summary.
    """
    if group_id in _generating_retros:
        log.info("Retrospective generation already in progress for group %s, skipping", group_id)
        return None
        
    _generating_retros.add(group_id)
    try:
        ws = group_workspace(group_id)
        # Put retro_anchor outside bot-accessible 'shared' workspace directory
        system_dir = ws.parent / ".system"
        system_dir.mkdir(parents=True, exist_ok=True)
        anchor_file = system_dir / "retro_anchor.json"
        
        last_retro_msg_id = 0
        if anchor_file.exists():
            try:
                data = json.loads(anchor_file.read_text(encoding="utf-8"))
                last_retro_msg_id = data.get("last_retro_msg_id", 0)
            except Exception:
                log.warning("Failed to parse retro_anchor.json for group %s", group_id)
                
        # Read messages starting after last_retro_msg_id
        # Limit to 151 to detect truncation and keep context window safe
        limit = 151
        async with get_db() as gdb:
            raw_messages = await get_messages(gdb, group_id, limit=limit, after_id=last_retro_msg_id)
            
        # Filter out soft-deleted and automated pump messages to prioritize core dialog
        messages = [m for m in raw_messages if not m.get("is_deleted") and not m.get("is_auto_reply")]
        
        if not messages:
            log.info("No new dialog messages for retrospective in group %s", group_id)
            return None
            
        # Compute maximum msg ID in the queried batch (including auto-replies) for next anchor check
        max_msg_id = max(m["id"] for m in raw_messages)
        
        truncated_marker = ""
        if len(messages) > 150:
            messages = messages[-150:]
            truncated_marker = "[工作历史过长，系统已截断前部较旧的消息，仅保留最近的 150 条消息进行分析。]\n\n"
            
        # Format messages for the prompt
        formatted = []
        for m in messages:
            name = m.get("sender_name") or "User"
            content = m.get("content") or ""
            if len(content) > 2000:
                content = content[:2000] + "... [内容超长已截断]"
            formatted.append(f"{name}: {content}")
            
        history_text = truncated_marker + "\n".join(formatted)
        
        # Fetch members from central DB to pick appropriate LLM model
        async with global_db() as cdb:
            members = await get_members(cdb, group_id)
            
        from core.recap.generator import _pick_provider_model
        provider, model = _pick_provider_model(members)
        
        user_message = (
            "以下是自上次复盘以来群组内发生的所有开发活动 and 讨论日志：\n\n"
            f"{history_text}\n\n"
            "请生成项目复盘 markdown 文档。"
        )
        
        log.info("Calling LLM (%s/%s) to generate retrospective for group %s", provider, model, group_id)
        res = await call_ai_once(
            system_prompt=_RETRO_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
            provider=provider, model=model, temperature=0.3, max_tokens=2048
        )
        content = (res.get("content") if isinstance(res, dict) else str(res)) or ""
        content = content.strip()
        if not content:
            log.warning("Generated retrospective is empty for group %s", group_id)
            return None
            
        # Extract Executive Summary to override groups.away_summary
        summary_text = ""
        m = re.search(r"## 1\.\s*最终成果摘要.*?\n(.*?)(?=\n## 2\.)", content, re.DOTALL)
        if m:
            summary_text = m.group(1).strip()
            summary_text = re.sub(r"^[-*#\s\d\.]+", "", summary_text)  # Remove list markers
            summary_text = summary_text.split("\n")[0].strip()  # Take the first line/sentence
            
        if not summary_text:
            summary_text = f"项目开发阶段已结束，详细设计决策与技术债已记录在 retros/run_{last_retro_msg_id}.md。"
            
        # Write retros/run_<anchor>.md
        retros_dir = ws / "retros"
        retros_dir.mkdir(parents=True, exist_ok=True)
        retro_file = retros_dir / f"run_{last_retro_msg_id}.md"
        retro_file.write_text(content, encoding="utf-8")
        
        # Overwrite RETRO_LATEST.md at workspace root for easy human viewing
        latest_file = ws / "RETRO_LATEST.md"
        latest_file.write_text(content, encoding="utf-8")
        
        log.info("Retrospective generated and saved successfully for group %s: %s", group_id, retro_file)
        
        # Cache summary_text as groups.away_summary in Central DB
        async with write_connect(db.DB_PATH) as db_conn:
            await db_conn.execute(
                "UPDATE groups SET away_summary = ? WHERE id = ?",
                (summary_text, group_id)
            )
            await db_conn.commit()
            
        # Broadcast summary updates to frontend
        await bus.broadcast(group_id, {
            "type": "recap_updated",
            "group_id": group_id,
            "away_summary": summary_text
        })
        
        # Save new anchor file to prevent repeated processing
        anchor_file.write_text(json.dumps({"last_retro_msg_id": max_msg_id}), encoding="utf-8")
        return content
        
    except Exception as e:
        log.error("Failed to generate retrospective for group %s: %r", group_id, e, exc_info=True)
        return None
    finally:
        _generating_retros.discard(group_id)
