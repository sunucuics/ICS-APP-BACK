#!/bin/bash

# ICS Backend Deploy Script
# Kullanım: ./deploy.sh

set -e

echo "🚀 ICS Backend Deploy Başlıyor..."

# Mevcut proje ID'sini al
PROJECT_ID=$(gcloud config get-value project 2>/dev/null)
if [ -z "$PROJECT_ID" ]; then
    echo "❌ Hata: gcloud projesi ayarlanmamış!"
    echo "Lütfen şu komutu çalıştırın: gcloud config set project YOUR_PROJECT_ID"
    exit 1
fi

echo "📋 Proje ID: $PROJECT_ID"
echo "📍 Region: europe-west1"

# 1. Build ve push
echo "📦 Docker image build ediliyor..."
gcloud builds submit --tag gcr.io/${PROJECT_ID}/ics-backend:latest .

# 2. Deploy
echo "🚀 Yeni revision deploy ediliyor..."
gcloud run deploy ics-backend \
  --image gcr.io/${PROJECT_ID}/ics-backend:latest \
  --region europe-west1 \
  --platform managed \
  --allow-unauthenticated \
  --port 8080 \
  --memory 1Gi \
  --cpu 1 \
  --max-instances 10 \
  --min-instances 0 \
  --timeout 300 \
  --concurrency 80 \
  --set-env-vars-from-file .env 2>/dev/null || echo "⚠️  .env dosyası bulunamadı, environment variables manuel ayarlanmalı"

echo "🎉 Deploy tamamlandı!"
echo "📱 Backend URL'ini almak için: gcloud run services describe ics-backend --region europe-west1 --format 'value(status.url)'"
