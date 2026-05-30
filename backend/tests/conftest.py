"""Shared pytest fixtures / test-suite hygiene.

`db/writer.py` caches a process-wide aiosqlite write connection per event loop
(`_state[id(loop)]`) and only closes it via `aclose_writer()` on app shutdown.
Tests built on `IsolatedAsyncioTestCase` get a fresh loop each and never call
`aclose_writer()`, so any test that hits `write_connect()` (e.g. react_v1.run)
leaks a connection whose **non-daemon** thread outlives the loop and blocks
`threading._shutdown` — the interpreter hangs at exit even though every test
passed. This fixture stops those orphaned threads after each test.

aiosqlite's worker thread blocks on a queue until it receives a stop sentinel;
`_stop_running()` enqueues that sentinel (thread-safe, no running loop needed),
so we can drain leaked connections without their original event loop.
"""
import pytest


@pytest.fixture(autouse=True)
def _close_leaked_writer_connections():
    yield
    try:
        from db import writer
    except Exception:
        return
    state = getattr(writer, "_state", None)
    if not state:
        return
    for st in list(state.values()):
        conn = st.get("conn") if isinstance(st, dict) else None
        if conn is None:
            continue
        try:
            conn._stop_running()
            conn.join(timeout=2)
        except Exception:
            pass
    state.clear()
