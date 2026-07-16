from executors.plugins.tool_loop_v1 import _completion_deadline


def _schema(name: str) -> dict:
    return {"function": {"name": name}}


def test_completion_deadline_warns_before_final_iterations():
    schemas = [_schema("edit_file"), _schema("create_pr")]

    selected, suffix = _completion_deadline(
        schemas,
        remaining_iterations=8,
        require_pull_request=True,
        completion_signal_seen=False,
    )

    assert selected == schemas
    assert "call create_pr" in suffix


def test_completion_deadline_reserves_final_iterations_for_control_tools():
    schemas = [
        _schema("edit_file"),
        _schema("create_pr"),
        _schema("signal_stage_done"),
        _schema("signal_rework"),
    ]

    selected, _ = _completion_deadline(
        schemas,
        remaining_iterations=3,
        require_pull_request=True,
        completion_signal_seen=False,
    )

    assert [item["function"]["name"] for item in selected] == [
        "create_pr",
        "signal_stage_done",
        "signal_rework",
    ]


def test_completion_deadline_does_not_affect_regular_agents():
    schemas = [_schema("edit_file")]

    selected, suffix = _completion_deadline(
        schemas,
        remaining_iterations=1,
        require_pull_request=False,
        completion_signal_seen=False,
    )

    assert selected == schemas
    assert suffix == ""
