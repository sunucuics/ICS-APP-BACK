#!/bin/bash

# ICS Backend Deploy Script
# Kullanım: ./deploy.sh

set -e

echo "🚀 ICS Backend Deploy Başlıyor..."

# 1. Build ve push
echo "📦 Docker image build ediliyor..."
gcloud builds submit --tag gcr.io/ics-app-f2598/ics-backend:latest .

# 2. Deploy
echo "🚀 Yeni revision deploy ediliyor..."
gcloud run deploy ics-backend \
  --image gcr.io/ics-app-f2598/ics-backend:latest \
  --region europe-west1 \
  --platform managed \
  --allow-unauthenticated \
  --port 8080 \
  --memory 1Gi \
  --cpu 1 \
  --max-instances 10 \
  --min-instances 0 \
  --timeout 300 \
  --concurrency 80

echo "🎉 Deploy tamamlandı!"
echo "📱 Backend URL: https://ics-backend-kp62cip2va-ew.a.run.app"
