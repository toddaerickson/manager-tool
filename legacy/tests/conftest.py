"""Shared fixtures for Manager Tool tests."""

import os
import sys
import tempfile
import pytest

# Ensure project root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Use a fresh temporary SQLite database for each test.

    init_db() is normally guarded against repeated execution within a
    process (P4.2), so we pass force=True here to ensure each test gets a
    fully-initialised schema in its own tmp_path."""
    import database as db

    db_path = str(tmp_path / "test_manager.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    # Force SQLite mode
    monkeypatch.setattr(db, "_USE_PG", False)
    # Reset the once-per-process guard so each test starts from a clean state.
    monkeypatch.setattr(db, "_INIT_DB_DONE", False)
    db.init_db(force=True)
    yield db_path
