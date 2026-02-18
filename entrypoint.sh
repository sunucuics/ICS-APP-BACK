#!/usr/bin/env bash
set -e

# Alembic varsa migrasyon çalıştır, yoksa atla
if [ -f "/app/alembic.ini" ] && [ -d "/app/alembic" ]; then
  echo "Running Alembic migrations..."
  alembic upgrade head || { echo "Alembic migration failed"; exit 1; }
else
  echo "Alembic not configured — skipping migrations."
fi

# Set Python path
export PYTHONPATH=/app:$PYTHONPATH

# Uvicorn
exec uvicorn backend.app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8080}" \
  --workers "${UVICORN_WORKERS:-1}" \
  --proxy-headers \
  --forwarded-allow-ips "*"
