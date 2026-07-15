#!/bin/sh
set -e

# Wait for PostgreSQL (skipped in USE_SQLITE mode), then migrate unless the
# deployment runs migrations as an explicit step (MIGRATE_ON_START=0).
python - <<'PY'
import os
import sys
import time

if os.environ.get("USE_SQLITE", "0") == "1":
    sys.exit(0)

import psycopg

dsn = (
    f"host={os.environ.get('DB_HOST', 'localhost')} "
    f"port={os.environ.get('DB_PORT', '5432')} "
    f"dbname={os.environ.get('POSTGRES_DB', 'orders')} "
    f"user={os.environ.get('POSTGRES_USER', 'orders')} "
    f"password={os.environ.get('POSTGRES_PASSWORD', 'dev-only-password')}"
)
deadline = time.monotonic() + 60
while True:
    try:
        psycopg.connect(dsn, connect_timeout=3).close()
        break
    except Exception as exc:
        if time.monotonic() > deadline:
            print(f"database never became ready: {exc}", file=sys.stderr)
            sys.exit(1)
        time.sleep(1)
PY

if [ "${MIGRATE_ON_START:-1}" = "1" ]; then
    python manage.py migrate --no-input
fi

exec "$@"
