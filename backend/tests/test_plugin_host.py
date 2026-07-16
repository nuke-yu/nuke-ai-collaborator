"""Unit tests for plugin host worker selection."""

from types import SimpleNamespace

from plugins.host import PluginHost
from runtime.ipc.protocol import MCP_COLLECTOR_ID


def _host(workers, stats=None):
    supervisor = SimpleNamespace(_workers=workers, _worker_stats=stats or {})
    return PluginHost(app=object(), supervisor=supervisor)


def test_least_loaded_never_selects_mcp_collector():
    host = _host(
        {"w0": object(), MCP_COLLECTOR_ID: object()},
        {"w0": {"bg": {"active_tasks": 2}}},
    )

    assert host.pick_worker("least_loaded") == "w0"


def test_random_and_fallback_never_select_mcp_collector(monkeypatch):
    host = _host({MCP_COLLECTOR_ID: object(), "w0": object()})
    monkeypatch.setattr("random.choice", lambda workers: workers[0])

    assert host.pick_worker("random") == "w0"
    assert host.pick_worker("unknown") == "w0"


def test_least_loaded_prefers_reported_stats_over_missing_stats():
    host = _host(
        {"new-worker": object(), "w0": object(), "w1": object()},
        {
            "w0": {"bg": {"active_tasks": 3}},
            "w1": {"bg": {"active_tasks": 1}},
        },
    )

    assert host.pick_worker("least_loaded") == "w1"


def test_least_loaded_uses_group_count_to_break_task_ties():
    host = _host(
        {"w0": object(), "w1": object(), "w2": object()},
        {
            "w0": {
                "bg": {"active_tasks": 0},
                "lifecycle": {"active_groups_count": 4},
            },
            "w1": {
                "bg": {"active_tasks": 0},
                "lifecycle": {"active_groups_count": 2},
            },
            "w2": {
                "bg": {"active_tasks": 1},
                "lifecycle": {"active_groups_count": 0},
            },
        },
    )

    assert host.pick_worker("least_loaded") == "w1"


def test_returns_none_when_only_collector_is_connected():
    host = _host({MCP_COLLECTOR_ID: object()})

    assert host.pick_worker() is None
