import sqlite3

import pytest

from memory.adapters.projections.chroma_maintenance import (
    ChromaCompatibilityError,
    backup_store,
    inspect_store,
    quarantine_store,
    require_compatible_store,
)


def _store(path, migrations=True):
    path.mkdir()
    with sqlite3.connect(path / "chroma.sqlite3") as conn:
        if migrations:
            conn.execute("CREATE TABLE migrations (dir TEXT, version INTEGER)")
            conn.execute("INSERT INTO migrations VALUES ('sysdb', 7)")


def test_inspect_store_reports_schema_without_opening_chroma(tmp_path):
    store = tmp_path / "chroma"
    _store(store)

    report = inspect_store(store, "1.5.9")

    assert report["compatible"] is True
    assert report["schema_version"] == 7
    assert report["runtime_version"] == "1.5.9"


def test_incompatible_store_is_refused_before_write(tmp_path):
    store = tmp_path / "chroma"
    _store(store, migrations=False)

    with pytest.raises(ChromaCompatibilityError, match="unsafe to open"):
        require_compatible_store(store, "1.5.9")


def test_expected_runtime_version_is_enforced(tmp_path):
    store = tmp_path / "chroma"
    _store(store)

    with pytest.raises(ChromaCompatibilityError, match="does not match"):
        require_compatible_store(store, "1.5.9", "0.5.0")


def test_backup_copies_sqlite_and_index_files(tmp_path):
    store = tmp_path / "chroma"
    _store(store)
    index = store / "index"
    index.mkdir()
    (index / "data.bin").write_bytes(b"vector data")

    backup = backup_store(store, tmp_path / "backups")

    assert (backup / "chroma.sqlite3").is_file()
    assert (backup / "index" / "data.bin").read_bytes() == b"vector data"


def test_unknown_schema_is_refused_by_compatibility_matrix(tmp_path):
    store = tmp_path / "chroma"
    _store(store)
    with sqlite3.connect(store / "chroma.sqlite3") as conn:
        conn.execute("UPDATE migrations SET version=99")

    assert inspect_store(store, "1.5.9")["compatible"] is False


def test_quarantine_moves_store_without_deleting_it(tmp_path):
    store = tmp_path / "chroma"
    _store(store)

    quarantined = quarantine_store(store)

    assert not store.exists()
    assert (quarantined / "chroma.sqlite3").is_file()
