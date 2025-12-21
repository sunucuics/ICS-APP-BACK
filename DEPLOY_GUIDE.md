# 🚀 Backend Deploy Rehberi

## Ön Hazırlık

### 1. Google Cloud Projesi Ayarlama

```bash
# Projeyi ayarla
gcloud config set project true-upgrade-470306-c4

# Authentication (eğer yapılmadıysa)
gcloud auth login
gcloud auth application-default login
```

### 2. Gerekli API'leri Aktif Et

```bash
# Cloud Build API
gcloud services enable cloudbuild.googleapis.com

# Cloud Run API
gcloud services enable run.googleapis.com

# Container Registry API
gcloud services enable containerregistry.googleapis.com
```

## Deploy Yöntemleri

### Yöntem 1: Cloud Shell'den (Önerilen) ⭐

1. Google Cloud Console'da Cloud Shell'i açın
2. Projeyi ayarlayın:
   ```bash
   gcloud config set project true-upgrade-470306-c4
   ```
3. Repo'yu yükleyin (git clone veya dosya yükleme)
4. Deploy scriptini çalıştırın:
   ```bash
   cd ICS-APP-BACK
   ./deploy.sh
   ```

### Yöntem 2: Local Terminalden

1. Local terminalde projeye gidin:
   ```bash
   cd /Users/berkeseker/Documents/Repositories/ICS-APP-BACK
   ```

2. gcloud authentication yapın:
   ```bash
   gcloud auth login
   gcloud config set project true-upgrade-470306-c4
   ```

3. Deploy scriptini çalıştırın:
   ```bash
   ./deploy.sh
   ```

## Environment Variables Ayarlama

Cloud Run'da environment variables ayarlamak için:

### Yöntem 1: .env dosyasından (önerilen)

```bash
gcloud run services update ics-backend \
  --region europe-west1 \
  --update-env-vars-from-file .env
```

### Yöntem 2: Manuel olarak

```bash
gcloud run services update ics-backend \
  --region europe-west1 \
  --set-env-vars "FIREBASE_PROJECT_ID=your-project-id,FIREBASE_WEB_API_KEY=your-key"
```

### Yöntem 3: Secret Manager (Production için önerilen)

```bash
# Secret oluştur
echo -n "your-secret-value" | gcloud secrets create firebase-web-api-key --data-file=-

# Secret'ı environment variable olarak ekle
gcloud run services update ics-backend \
  --region europe-west1 \
  --update-secrets FIREBASE_WEB_API_KEY=firebase-web-api-key:latest
```

## Deploy Sonrası

### Backend URL'ini Öğrenme

```bash
gcloud run services describe ics-backend \
  --region europe-west1 \
  --format 'value(status.url)'
```

### Logları Görüntüleme

```bash
gcloud run services logs read ics-backend \
  --region europe-west1 \
  --limit 50
```

### Servisi Test Etme

```bash
# Health check
curl https://YOUR-BACKEND-URL/healthz

# API docs
open https://YOUR-BACKEND-URL/docs
```

## Sorun Giderme

### Build Hatası
```bash
# Build loglarını kontrol et
gcloud builds list --limit=5
gcloud builds log BUILD_ID
```

### Deploy Hatası
```bash
# Revision'ları kontrol et
gcloud run revisions list --service ics-backend --region europe-west1

# Son revision'ın loglarını gör
gcloud run services logs read ics-backend --region europe-west1
```

### Environment Variables Kontrol
```bash
gcloud run services describe ics-backend \
  --region europe-west1 \
  --format 'value(spec.template.spec.containers[0].env)'
```

