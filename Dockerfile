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

# Pip'i upgrade et ve root user uyarısını bastır
RUN pip install --upgrade pip --root-user-action=ignore

# Bağımlılıklar (cache için ayrı katman)
COPY requirements.txt .
RUN pip install --root-user-action=ignore -r requirements.txt

# Uygulama + opsiyonel dosyalar
COPY backend/ ./backend/
COPY entrypoint.sh ./
COPY .env .env
COPY firebase_service_account.json ./backend/firebase_service_account.json

# Windows satır sonu düzelt + izinler
RUN sed -i 's/\r$//' ./entrypoint.sh || true \
 && chmod +x ./entrypoint.sh \
 && chown -R appuser:appuser /app

USER appuser
EXPOSE ${PORT}
ENTRYPOINT ["./entrypoint.sh"]
