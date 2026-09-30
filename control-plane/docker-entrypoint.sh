#!/bin/sh
set -e
# Apply schema migrations before serving. Safe on every start: alembic is a
# no-op at head. Set SKIP_MIGRATIONS=1 when migrations run as a release step.
if [ "${SKIP_MIGRATIONS:-0}" != "1" ]; then
  alembic upgrade head
fi
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --workers "${WEB_CONCURRENCY:-2}"
