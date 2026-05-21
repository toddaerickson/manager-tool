"""Regression for AUDIT M6 / P5 — flag silent `except: pass` and bare
`except Exception: pass` patterns. The audit specifically called out:
- auth.py:59-60 (redirect URI inference)
- database.py:102-103 (Streamlit secrets in _get_pg_url)
- database.py:49-50 (Fernet init — fixed in P0.2)
- database.py:68-69 (Fernet decrypt — fixed in P0.2)

This test scans the relevant project modules for any except block whose
body is just `pass` (the canonical silent-failure shape). New code is
expected to log via `logger.debug/info/warning/error/exception` or
re-raise."""

import ast
import io
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Production modules in scope. Tests are excluded — synthetic try/except in
# tests is fine.
SCANNED_FILES = (
    "auth.py",
    "calendar_service.py",
    "coaching.py",
    "database.py",
    "web_app.py",
)


def _silent_handlers(path: Path) -> list[tuple[int, str]]:
    """Return [(lineno, except_label)] for every `except: pass` /
    `except SomeExc: pass` block in the given file."""
    src = path.read_text()
    tree = ast.parse(src)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        # Body is exactly one Pass statement → silent.
        if (len(node.body) == 1 and isinstance(node.body[0], ast.Pass)):
            label = "bare" if node.type is None else ast.unparse(node.type)
            found.append((node.lineno, label))
    return found


@pytest.mark.parametrize("filename", SCANNED_FILES)
def test_no_silent_except_pass(filename):
    found = _silent_handlers(ROOT / filename)
    assert not found, (
        f"{filename} contains silent except-pass blocks — every except "
        f"must log or re-raise (AUDIT M6): {found}"
    )
