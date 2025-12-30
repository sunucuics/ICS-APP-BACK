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
echo "📍 Region: europe-west1"

# Unique tag oluştur (timestamp)
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
IMAGE_TAG="gcr.io/${PROJECT_ID}/ics-backend:${TIMESTAMP}"
LATEST_TAG="gcr.io/${PROJECT_ID}/ics-backend:latest"

# 1. Build ve push (hem timestamp hem latest tag ile)
echo "📦 Docker image build ediliyor..."
gcloud builds submit --tag ${IMAGE_TAG} --tag ${LATEST_TAG} .

# 2. Deploy (timestamp tag kullanarak - Cloud Run'ın yeni revision oluşturmasını garanti eder)
echo "🚀 Yeni revision deploy ediliyor..."
gcloud run deploy ics-backend \
  --image ${IMAGE_TAG} \
  --region europe-west1 \
  --platform managed \
  --allow-unauthenticated \
  --port 8080

echo "🎉 Deploy tamamlandı!"
