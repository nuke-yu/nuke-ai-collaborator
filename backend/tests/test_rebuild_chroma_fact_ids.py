from scripts.rebuild_chroma_fact_ids import _rebuild_vector_ids


def test_rebuild_deletes_only_target_bot_facts_and_reflections():
    rows = {
        "ids": ["fact-a", "refl-a", "tools-a", "fact-other-group", "fact-other-bot"],
        "metadatas": [
            {"group_id": 3, "bot_id": 10, "mem_type": "fact"},
            {"group_id": 3, "bot_id": 10, "mem_type": "reflection"},
            {"group_id": 3, "bot_id": 10, "mem_type": "tool_episode"},
            {"group_id": 4, "bot_id": 10, "mem_type": "fact"},
            {"group_id": 3, "bot_id": 11, "mem_type": "fact"},
        ],
    }

    selected = _rebuild_vector_ids(rows, {(3, 10)})

    assert selected == ["fact-a", "refl-a"]
