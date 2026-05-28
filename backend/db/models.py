import json


def _parse_json(val):
    try:
        return json.loads(val) if val else {}
    except Exception:
        return {}


def _row_to_member(r):
    return {
        "id": r[0], "group_id": r[1], "name": r[2], "type": r[3],
        "role": r[4], "system_prompt": r[5], "avatar_color": r[6],
        "model_provider": r[7] if len(r) > 7 else "deepseek",
        "model_name": r[8] if len(r) > 8 else "deepseek-chat",
        "auto_reply": r[9] if len(r) > 9 else None,
        "context_cleared_at": r[10] if len(r) > 10 else None,
        "temperature": r[11] if len(r) > 11 else 0.7,
        "max_tokens": r[12] if len(r) > 12 else 4096,
        "personality_prompt": r[13] if len(r) > 13 else None,
        "executor_id": r[14] if len(r) > 14 else "simple_v1",
        "executor_config": _parse_json(r[15]) if len(r) > 15 else {},
        "done_keyword": r[16] if len(r) > 16 else None,
    }


_MSG_SQL = """
    SELECT m.id, m.group_id, m.member_id, m.content, m.created_at,
           mb.name, mb.type, mb.avatar_color,
           m.reply_to_id, rm.content, rmb.name,
           m.edited_at, m.is_deleted,
           m.file_url, m.file_name, m.file_size, m.file_type,
           m.is_auto_reply
    FROM messages m
    JOIN members mb ON m.member_id = mb.id
    LEFT JOIN messages rm ON m.reply_to_id = rm.id
    LEFT JOIN members rmb ON rm.member_id = rmb.id
"""


def _row_to_msg(r):
    reply_to = {"id": r[8], "sender_name": r[10], "content": r[9]} if r[8] else None
    created_at = r[4]
    if created_at and "Z" not in created_at and "+" not in created_at:
        created_at = created_at.replace(" ", "T") + "Z"
    return {
        "id": r[0], "group_id": r[1], "member_id": r[2], "content": r[3],
        "created_at": created_at, "sender_name": r[5], "sender_type": r[6],
        "avatar_color": r[7], "reply_to": reply_to,
        "edited_at": r[11], "is_deleted": bool(r[12]),
        "file_url": r[13], "file_name": r[14], "file_size": r[15], "file_type": r[16],
        "is_auto_reply": bool(r[17]) if len(r) > 17 else False,
    }
