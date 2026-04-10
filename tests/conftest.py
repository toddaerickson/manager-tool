"""Shared fixtures for Manager Tool tests."""

import os
import sys
import tempfile
import pytest

# Ensure project root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Use a fresh temporary SQLite database for each test."""
    import database as db

    db_path = str(tmp_path / "test_manager.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    # Force SQLite mode
    monkeypatch.setattr(db, "_USE_PG", False)
    db.init_db()
    yield db_path
