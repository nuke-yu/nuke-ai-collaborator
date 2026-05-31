"""CELL-04: per-call DB routing via a contextvar.

A worker owns many per-group private DBs + one read-only central DB. Instead of
threading a db handle through every function signature, the worker binds the
"current group DB" for the duration of handling that group's work; connect() and
write_connect() resolve their path from this contextvar when none is passed
explicitly. contextvars are copied into tasks at creation, so bg.spawn()'d
dispatch chains inherit the binding automatically.

Default is None → the caller's module DB_PATH, so single-process / pre-split
usage is unchanged. Central-domain access (groups / members / app_config /
templates) must bypass this binding via db.global_db().

Dependency-pure: stdlib only, imports nothing from the db package (avoids a cycle
since both db/__init__.py and db/writer.py import from here).
"""
import contextvars
from contextlib import contextmanager

# None → fall back to the caller-provided default (central / module DB_PATH).
current_db_path: contextvars.ContextVar = contextvars.ContextVar(
    "nuke_current_db_path", default=None
)


def resolve(path: str | None, default: str) -> str:
    """Explicit path wins; else the bound current group DB; else `default`."""
    if path is not None:
        return path
    return current_db_path.get() or default


@contextmanager
def bind_db(path: str):
    """Bind the current group DB for the enclosing async call tree (and its child
    tasks). Reset on exit so bindings don't leak across groups."""
    token = current_db_path.set(path)
    try:
        yield
    finally:
        current_db_path.reset(token)
