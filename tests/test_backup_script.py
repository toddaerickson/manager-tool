"""Tests for scripts/backup.sh. Focused on the security-critical bits: the
URL parser must produce shell-safe output that doesn't allow injection from a
maliciously crafted DATABASE_URL, and the password must never end up on argv."""

import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKUP_SH = ROOT / "scripts" / "backup.sh"


def test_backup_script_is_syntactically_valid():
    """`bash -n` parses the script."""
    res = subprocess.run(["bash", "-n", str(BACKUP_SH)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


def test_backup_script_does_not_pass_database_url_as_argv():
    """No `pg_dump "$DATABASE_URL"` — the URL must be parsed into PG* env vars
    so the password never lands on /proc argv (regression for AUDIT H7)."""
    src = BACKUP_SH.read_text()
    assert 'pg_dump "$DATABASE_URL"' not in src
    assert "pg_dump $DATABASE_URL" not in src
    # Positive: PG* env vars are exported.
    assert "PGPASSWORD" in src
    assert "PGHOST" in src


def test_backup_script_chmods_directory_700():
    """backups/ is private (mode 700)."""
    src = BACKUP_SH.read_text()
    assert re.search(r'chmod\s+700\s+"\$BACKUP_DIR"', src)


def test_backup_script_writes_dump_files_mode_600():
    """Dump files (encrypted or plaintext) end up mode 600."""
    src = BACKUP_SH.read_text()
    assert "chmod 600" in src


def test_backup_script_emits_sha256_sidecars():
    """Each dump gets a SHA-256 checksum so corruption is detectable."""
    src = BACKUP_SH.read_text()
    assert re.search(r'sha256sum\s+"\$DUMP_CUSTOM"', src)
    assert re.search(r'sha256sum\s+"\$DUMP_PLAIN"', src)


def test_backup_script_supports_optional_gpg_encryption():
    """When BACKUP_GPG_PASSPHRASE is set, dumps are pipe-encrypted with
    gpg --symmetric --cipher-algo AES256."""
    src = BACKUP_SH.read_text()
    assert "BACKUP_GPG_PASSPHRASE" in src
    assert "gpg --symmetric --cipher-algo AES256" in src


def test_backup_script_supports_optional_off_host_copy():
    """BACKUP_S3_PATH triggers an off-host upload."""
    src = BACKUP_SH.read_text()
    assert "BACKUP_S3_PATH" in src
    assert "aws s3 cp" in src


def test_url_parser_shell_quote_roundtrip():
    """Mirrors the inline parser in backup.sh. A password containing shell
    metacharacters and embedded single quotes must round-trip through `eval`
    byte-for-byte. Regression: this is the seam where a malformed URL could
    inject shell commands if quoting were sloppy."""
    from urllib.parse import urlparse, unquote

    def shq(s):
        return "'" + str(s).replace("'", "'\\''") + "'"

    p = urlparse(
        "postgres://u%40org:p'a%24%24%20w%3Bord@db.example.com:5432/mt?sslmode=require"
    )
    quoted_pw = shq(unquote(p.password))
    res = subprocess.run(
        ["bash", "-c", f"export PGPASSWORD={quoted_pw}; printf '%s' \"$PGPASSWORD\""],
        capture_output=True, text=True,
    )
    assert res.returncode == 0
    assert res.stdout == "p'a$$ w;ord"

    # Hostname/db parsing
    assert p.hostname == "db.example.com"
    assert p.port == 5432
    assert (p.path or "/").lstrip("/") == "mt"
    assert unquote(p.username) == "u@org"


def test_url_parser_rejects_no_password_gracefully():
    """A URL with no password should produce empty PGPASSWORD, not crash."""
    from urllib.parse import urlparse, unquote

    def shq(s):
        return "'" + str(s).replace("'", "'\\''") + "'"

    p = urlparse("postgresql://localhost/test")
    pw = unquote(p.password) if p.password else ""
    assert shq(pw) == "''"
