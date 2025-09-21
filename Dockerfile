FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

# Temel paketler (psycopg2, curl vs. için)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN useradd -m appuser

# Bağımlılıklar (cache için ayrı katman)
COPY requirements.txt .
RUN pip install -r requirements.txt

# Uygulama + opsiyonel dosyalar (alembic varsa gelir, yoksa sorun olmaz)
COPY backend/ ./backend/
COPY firebase_service_account.json ./backend/firebase_service_account.json
COPY entrypoint.sh ./

# Debug: Check if firebase service account file was copied
RUN ls -la /app/backend/ | grep firebase || echo "Firebase file not found!"

# Windows satır sonu düzelt + izinler
RUN sed -i 's/\r$//' ./entrypoint.sh || true \
 && chmod +x ./entrypoint.sh \
 && chown -R appuser:appuser /app

USER appuser
EXPOSE ${PORT}
ENTRYPOINT ["./entrypoint.sh"]
