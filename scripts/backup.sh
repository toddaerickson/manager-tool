#!/bin/bash
# Manager Tool — PostgreSQL backup script
# Usage: bash scripts/backup.sh
# Requires: postgresql-client (sudo apt install postgresql-client)
# Reads DATABASE_URL from .streamlit/secrets.toml or environment

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="$PROJECT_DIR/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

mkdir -p "$BACKUP_DIR"

# Resolve DATABASE_URL: env var first, then .streamlit/secrets.toml
if [ -z "${DATABASE_URL:-}" ]; then
    SECRETS_FILE="$PROJECT_DIR/.streamlit/secrets.toml"
    if [ -f "$SECRETS_FILE" ]; then
        DATABASE_URL=$(grep '^DATABASE_URL' "$SECRETS_FILE" | sed 's/^DATABASE_URL\s*=\s*"\(.*\)"/\1/')
    fi
fi

if [ -z "${DATABASE_URL:-}" ]; then
    echo "ERROR: DATABASE_URL not found in environment or .streamlit/secrets.toml"
    exit 1
fi

echo "Backing up to $BACKUP_DIR/neon_backup_$TIMESTAMP.dump ..."
pg_dump "$DATABASE_URL" --format=custom --file="$BACKUP_DIR/neon_backup_$TIMESTAMP.dump"

echo "Creating plain SQL copy ..."
pg_dump "$DATABASE_URL" --format=plain --file="$BACKUP_DIR/neon_backup_$TIMESTAMP.sql"

# Keep only the last 4 backup pairs
echo "Cleaning old backups (keeping last 4) ..."
ls -tp "$BACKUP_DIR"/neon_backup_*.dump 2>/dev/null | tail -n +5 | xargs -r rm --
ls -tp "$BACKUP_DIR"/neon_backup_*.sql 2>/dev/null | tail -n +5 | xargs -r rm --

echo "Done: neon_backup_$TIMESTAMP.dump + .sql"
