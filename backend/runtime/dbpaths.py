"""Group/central DB path mapping (V3). The runtime layer is allowed to know the
workspace layout; the db layer stays path-agnostic."""
import os

from workspace import layout


def group_db_path(group_id: int) -> str:
    return os.path.join(str(layout.group_dir(group_id)), "chat.db")


def central_db_path() -> str:
    import db
    return db.DB_PATH
