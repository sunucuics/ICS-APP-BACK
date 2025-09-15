#!/usr/bin/env bash
set -e

# Alembic varsa migrasyon çalıştır, yoksa atla
if [ -f "/app/alembic.ini" ] && [ -d "/app/alembic" ]; then
  echo "Running Alembic migrations..."
  alembic upgrade head || { echo "Alembic migration failed"; exit 1; }
else
  echo "Alembic not configured — skipping migrations."
fi

# Firebase key (opsiyonel) kontrol
if [ -n "${GOOGLE_APPLICATION_CREDENTIALS}" ] && [ ! -f "${GOOGLE_APPLICATION_CREDENTIALS}" ]; then
  echo "WARNING: GOOGLE_APPLICATION_CREDENTIALS set but file not found: ${GOOGLE_APPLICATION_CREDENTIALS}"
fi

# Uvicorn
exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8080}" \
  --proxy-headers \
  --forwarded-allow-ips "*"
