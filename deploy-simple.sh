#!/bin/bash

# ICS Backend Deploy Script (Simple)
# Kullanım: ./deploy-simple.sh

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
  --port 8080

echo "🎉 Deploy tamamlandı!"
