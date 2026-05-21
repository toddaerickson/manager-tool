#!/bin/bash
# Manager Tool — PostgreSQL backup script (hardened, P2.4 / AUDIT H7)
#
# Usage:
#     bash scripts/backup.sh
#
# Requires:
#     postgresql-client (apt install postgresql-client)
#
# Inputs (any of):
#     DATABASE_URL              full Postgres URL (env var preferred)
#     .streamlit/secrets.toml   fallback location, parsed via TOML-aware Python
#
# Optional hardening:
#     BACKUP_GPG_PASSPHRASE     symmetric-encrypt every dump via gpg
#     BACKUP_S3_PATH            s3:// or b2:// destination for off-host copies
#                               (requires `aws` CLI; uses `aws s3 cp`)
#
# Hardening over the previous version (AUDIT H7):
#   - DATABASE_URL is parsed into PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD
#     env vars and consumed by pg_dump from the env, NEVER argv. No password
#     leaks in `ps auxe` or shell history.
#   - backups/ is created with mode 700.
#   - Each dump gets a SHA-256 checksum sidecar (.sha256).
#   - When BACKUP_GPG_PASSPHRASE is set, dumps are pipe-encrypted with
#     gpg --symmetric --cipher-algo AES256; the plaintext is never written
#     to disk.
#   - When BACKUP_S3_PATH is set, the encrypted dump is shipped off-host.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="$PROJECT_DIR/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

# ---------------------------------------------------------------------------
# Resolve DATABASE_URL: env var first, then .streamlit/secrets.toml.
# We use Python (always present alongside the app) for TOML parsing instead
# of the previous fragile sed regex that broke on multi-line values.
# ---------------------------------------------------------------------------
if [ -z "${DATABASE_URL:-}" ]; then
    SECRETS_FILE="$PROJECT_DIR/.streamlit/secrets.toml"
    if [ -f "$SECRETS_FILE" ]; then
        DATABASE_URL=$(python3 - <<PY "$SECRETS_FILE"
import sys
try:
    import tomllib  # 3.11+
except ImportError:
    import tomli as tomllib  # type: ignore
with open(sys.argv[1], "rb") as f:
    data = tomllib.load(f)
print(data.get("DATABASE_URL", ""))
PY
)
    fi
fi

if [ -z "${DATABASE_URL:-}" ]; then
    echo "ERROR: DATABASE_URL not found in environment or .streamlit/secrets.toml" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Parse DATABASE_URL into PG* env vars. pg_dump reads these and the password
# never lands on argv (visible to other users via /proc).
# ---------------------------------------------------------------------------
eval "$(python3 - <<'PY' "$DATABASE_URL"
import sys
from urllib.parse import urlparse, unquote
p = urlparse(sys.argv[1])
def shq(s):
    # Single-quote-escape for safe sourcing.
    return "'" + str(s).replace("'", "'\\''") + "'"
print(f"export PGHOST={shq(p.hostname or '')}")
print(f"export PGPORT={shq(p.port or 5432)}")
print(f"export PGDATABASE={shq((p.path or '/').lstrip('/'))}")
print(f"export PGUSER={shq(unquote(p.username) if p.username else '')}")
print(f"export PGPASSWORD={shq(unquote(p.password) if p.password else '')}")
# sslmode (Neon needs require)
import urllib.parse as up
qs = dict(up.parse_qsl(p.query))
sslmode = qs.get("sslmode", "require")
print(f"export PGSSLMODE={shq(sslmode)}")
PY
)"

# Defensive: clear DATABASE_URL from this script's environment so we don't
# accidentally pass it to anything down the line.
unset DATABASE_URL

# ---------------------------------------------------------------------------
# Take the dump. We always start from a custom-format pg_dump (compact,
# parallel-restorable) and additionally produce a plain SQL companion.
# Both are encrypted in place when BACKUP_GPG_PASSPHRASE is set.
# ---------------------------------------------------------------------------
DUMP_BASE="$BACKUP_DIR/neon_backup_$TIMESTAMP"
DUMP_CUSTOM="$DUMP_BASE.dump"
DUMP_PLAIN="$DUMP_BASE.sql"

echo "Backing up (custom format) to $DUMP_CUSTOM ..."
if [ -n "${BACKUP_GPG_PASSPHRASE:-}" ]; then
    pg_dump --format=custom \
        | gpg --symmetric --cipher-algo AES256 --batch --yes \
              --passphrase-fd 0 \
              --output "$DUMP_CUSTOM.gpg" \
        <<<"$BACKUP_GPG_PASSPHRASE"
    chmod 600 "$DUMP_CUSTOM.gpg"
    DUMP_CUSTOM="$DUMP_CUSTOM.gpg"
else
    pg_dump --format=custom --file="$DUMP_CUSTOM"
    chmod 600 "$DUMP_CUSTOM"
fi

echo "Backing up (plain SQL) to $DUMP_PLAIN ..."
if [ -n "${BACKUP_GPG_PASSPHRASE:-}" ]; then
    pg_dump --format=plain \
        | gpg --symmetric --cipher-algo AES256 --batch --yes \
              --passphrase-fd 0 \
              --output "$DUMP_PLAIN.gpg" \
        <<<"$BACKUP_GPG_PASSPHRASE"
    chmod 600 "$DUMP_PLAIN.gpg"
    DUMP_PLAIN="$DUMP_PLAIN.gpg"
else
    pg_dump --format=plain --file="$DUMP_PLAIN"
    chmod 600 "$DUMP_PLAIN"
fi

# ---------------------------------------------------------------------------
# Checksums (SHA-256 sidecar per dump) so corruption is detectable.
# ---------------------------------------------------------------------------
sha256sum "$DUMP_CUSTOM" > "$DUMP_CUSTOM.sha256"
sha256sum "$DUMP_PLAIN"  > "$DUMP_PLAIN.sha256"

# ---------------------------------------------------------------------------
# Off-host copy (S3-compatible).
# ---------------------------------------------------------------------------
if [ -n "${BACKUP_S3_PATH:-}" ]; then
    echo "Uploading to $BACKUP_S3_PATH ..."
    if ! command -v aws >/dev/null 2>&1; then
        echo "WARNING: BACKUP_S3_PATH set but 'aws' CLI not found; skipping upload." >&2
    else
        aws s3 cp "$DUMP_CUSTOM"        "$BACKUP_S3_PATH/" --no-progress
        aws s3 cp "$DUMP_CUSTOM.sha256" "$BACKUP_S3_PATH/" --no-progress
        aws s3 cp "$DUMP_PLAIN"         "$BACKUP_S3_PATH/" --no-progress
        aws s3 cp "$DUMP_PLAIN.sha256"  "$BACKUP_S3_PATH/" --no-progress
    fi
fi

# ---------------------------------------------------------------------------
# Local rotation: keep last 4 of each artifact type.
# ---------------------------------------------------------------------------
echo "Cleaning old backups (keeping last 4) ..."
for pattern in "neon_backup_*.dump" "neon_backup_*.dump.gpg" \
               "neon_backup_*.sql" "neon_backup_*.sql.gpg" \
               "neon_backup_*.sha256"; do
    # shellcheck disable=SC2012
    ls -tp "$BACKUP_DIR"/$pattern 2>/dev/null | tail -n +5 | xargs -r rm --
done

echo "Done: $(basename "$DUMP_CUSTOM") + $(basename "$DUMP_PLAIN")"
