#!/usr/bin/env bash
# Regenerate docs/schema-overview.svg from src/kayak/db/models.py.
#
# Wrapper for reproducibility, not automated regeneration. Run when
# models.py changes structurally (new table, dropped column, renamed
# column, FK rewire). Comment-only / type-only edits don't need a regen.
#
# Prereqs: graphviz (`sudo apt install graphviz`) + eralchemy
# (`/home/pat/.venv/bin/pip install eralchemy graphviz`).
#
# Mechanism: ORM-side, not live-DB. Builds an in-memory schema via
# Base.metadata.create_all() into a temp SQLite file, then points
# eralchemy at it. schema_migrations is excluded (it's bookkeeping, not
# domain model). Generating from the live DB would also pick up
# whatever schema_migrations + drift is in prod; the ORM is the
# source of truth.

set -euo pipefail
cd "$(dirname "$0")/.."

: "${KAYAK_HOME:=/home/pat}"
[ -r /etc/kayak/env ] && . /etc/kayak/env

VENV="${KAYAK_VENV:-${KAYAK_HOME}/.venv}"
PYTHON="$VENV/bin/python3"
ERALCHEMY="$VENV/bin/eralchemy"
OUTPUT="docs/schema-overview.svg"

if ! command -v dot >/dev/null 2>&1; then
    echo "ERR: graphviz not installed (need 'dot' binary). Run: sudo apt install graphviz" >&2
    exit 1
fi
if [[ ! -x "$ERALCHEMY" ]]; then
    echo "ERR: eralchemy not installed in $VENV. Run: $VENV/bin/pip install eralchemy graphviz" >&2
    exit 1
fi

# Portable temp file: GNU `mktemp --suffix` isn't supported by BSD/macOS mktemp,
# so append the .db suffix to a bare mktemp template instead (works on both).
TMP_DB="$(mktemp)".db
trap 'rm -f "$TMP_DB"' EXIT

"$PYTHON" - <<EOF
from sqlalchemy import create_engine
from kayak.db.models import Base
eng = create_engine("sqlite:///$TMP_DB")
Base.metadata.create_all(eng)
EOF

"$ERALCHEMY" \
    -i "sqlite:///$TMP_DB" \
    -o "$OUTPUT" \
    --exclude-tables schema_migrations

echo "wrote $OUTPUT ($(wc -c <"$OUTPUT") bytes)"
