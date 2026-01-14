# Taksit Sorunu Çözüm Kılavuzu

## 🔍 Sorun
Localde aynı kredi kartı ile taksit seçenekleri çıkıyor, ancak TestFlight'ta (production) aynı kart ile sadece "Bu kart ile sadece tek çekim yapılabilir" mesajı görünüyor.

## 🎯 Sorunun Kökü

### 1. Environment Farkı
- **Local (Debug)**: `http://localhost:8000` - Local backend kullanılıyor
- **TestFlight (Release)**: `https://ics-backend-443215445942.europe-west1.run.app` - Production backend kullanılıyor

### 2. Backend Kodu Analizi
`backend/app/payments/paytr_direct.py` dosyasında `direct_installment_quote` fonksiyonu (satır 328-370):

```python
# Satır 348-356
card_type = (bres.get("cardType") or "").lower()  # credit / debit
brand = (bres.get("brand") or "").lower()

options: List[Dict[str, Any]] = [
    {"installment_count": 0, "rate_percent": 0, "total_tl": tl_str(base_amount), "per_installment_tl": tl_str(base_amount)}
]

if card_type != "credit" or not brand or brand == "none":
    return {"status": "success", "brand": brand, "cardType": card_type, "installments": options, "bin": bres}
```

**Kritik Nokta**: Eğer PayTR'den gelen yanıtta:
- `cardType != "credit"` VEYA
- `brand` boş/yok VEYA  
- `brand == "none"`

ise, sadece peşin seçeneği dönüyor (taksit yok).

## 🔧 Olası Sebepler ve Çözümler

### Sebep 1: PayTR Test/Production Mode Farkı

**Kontrol Edilmesi Gerekenler:**

1. **Cloud Run'da environment variables kontrolü:**
```bash
gcloud run services describe ics-backend --region europe-west1 --format="value(spec.template.spec.containers[0].env)"
```

2. **PAYTR_TEST_MODE değişkenini kontrol et:**
   - Local'de: `.env` dosyasında `PAYTR_TEST_MODE=1` (test mode)
   - Production'da: Cloud Run'da `PAYTR_TEST_MODE=0` veya hiç set edilmemiş olabilir

**Sorun**: PayTR'nin test ve production ortamlarında aynı BIN farklı sonuçlar verebilir!

### Sebep 2: PayTR Credentials Farkı

**Kontrol Edilmesi Gerekenler:**

1. Local ve Production'da farklı PayTR hesapları kullanılıyor olabilir:
   - `PAYTR_MERCHANT_ID`
   - `PAYTR_MERCHANT_KEY`
   - `PAYTR_MERCHANT_SALT`

2. Test hesabı ile production hesabının taksit anlaşmaları farklı olabilir.

### Sebep 3: BIN Sorgulama Hatası

PayTR'nin BIN Detail API'si production'da farklı yanıt veriyor olabilir.

## ✅ Çözüm Adımları

### Adım 1: Cloud Run Environment Variables Kontrolü

```bash
# Mevcut environment variables'ı listele
gcloud run services describe ics-backend \
  --region europe-west1 \
  --format="value(spec.template.spec.containers[0].env)"
```

### Adım 2: Environment Variables'ı Manuel Set Et

```bash
# PayTR ayarlarını Cloud Run'a ekle
gcloud run services update ics-backend \
  --region europe-west1 \
  --set-env-vars="PAYTR_TEST_MODE=1" \
  --set-env-vars="PAYTR_MERCHANT_ID=YOUR_MERCHANT_ID" \
  --set-env-vars="PAYTR_MERCHANT_KEY=YOUR_MERCHANT_KEY" \
  --set-env-vars="PAYTR_MERCHANT_SALT=YOUR_MERCHANT_SALT" \
  --set-env-vars="INSTALLMENT_SURCHARGE_PERCENT=15.0"
```

**ÖNEMLİ**: Yukarıdaki komutta `YOUR_MERCHANT_ID`, `YOUR_MERCHANT_KEY`, `YOUR_MERCHANT_SALT` değerlerini kendi PayTR bilgilerinizle değiştirin.

### Adım 3: Logging Ekle (Debug İçin)

Backend'e daha detaylı log eklemek için `paytr_direct.py` dosyasını güncelleyin:

```python
# Satır 332'den sonra ekleyin:
log.info("BIN_DETAIL_REQUEST bin=%s amount=%s", body.bin_number, base_amount)

bres = await paytr_bin_detail(body.bin_number)

# Satır 333'ten sonra ekleyin:
log.info("BIN_DETAIL_RESPONSE bin=%s response=%s", body.bin_number, bres)

if bres.get("status") != "success":
    log.warning("BIN_DETAIL_FAILED bin=%s reason=%s", body.bin_number, bres.get("err_msg") or bres.get("reason"))
    return {
        "status": "failed",
        "reason": bres.get("err_msg") or bres.get("reason") or "BIN tanımsız",
        "installments": [
            {
                "installment_count": 0,
                "rate_percent": 0,
                "total_tl": tl_str(base_amount),
                "per_installment_tl": tl_str(base_amount),
            }
        ],
        "bin": bres,
    }

card_type = (bres.get("cardType") or "").lower()
brand = (bres.get("brand") or "").lower()

# Bu satırı ekleyin:
log.info("BIN_DETAIL_PARSED bin=%s cardType=%s brand=%s", body.bin_number, card_type, brand)
```

### Adım 4: Test Kartı ile Kontrol

PayTR test kartlarını kullanarak test edin:
- **Test Kredi Kartı**: 4355 0840 0000 0001
- **Test Banka Kartı**: 5528 7900 0000 0001

### Adım 5: Production Loglarını Kontrol Et

```bash
# Cloud Run loglarını izle
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=ics-backend" \
  --limit 50 \
  --format json \
  --freshness 1h
```

Özellikle şu log satırlarını arayın:
- `BIN_DETAIL_REQUEST`
- `BIN_DETAIL_RESPONSE`
- `BIN_DETAIL_PARSED`
- `BIN_DETAIL_FAILED`

## 🎯 Hızlı Test

### Test 1: API'yi Doğrudan Çağır

```bash
# Production backend'e doğrudan BIN sorgusu yap
curl -X POST https://ics-backend-443215445942.europe-west1.run.app/paytr/direct/bin-detail \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "bin_number": "454358",
    "debug_on": 1
  }'
```

### Test 2: Installment Quote Sorgusu

```bash
curl -X POST https://ics-backend-443215445942.europe-west1.run.app/paytr/direct/installment-quote \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "bin_number": "454358",
    "amount_tl": 100.0
  }'
```

Yanıtta `cardType` ve `brand` değerlerini kontrol edin.

## 📊 Beklenen Yanıt

### Başarılı (Taksitli Kart):
```json
{
  "status": "success",
  "brand": "visa",
  "cardType": "credit",
  "installments": [
    {
      "installment_count": 0,
      "rate_percent": 0,
      "total_tl": "100.00",
      "per_installment_tl": "100.00"
    },
    {
      "installment_count": 3,
      "rate_percent": 15.0,
      "total_tl": "115.00",
      "per_installment_tl": "38.33"
    }
  ]
}
```

### Başarısız (Banka Kartı veya Brand=none):
```json
{
  "status": "success",
  "brand": "none",
  "cardType": "debit",
  "installments": [
    {
      "installment_count": 0,
      "rate_percent": 0,
      "total_tl": "100.00",
      "per_installment_tl": "100.00"
    }
  ]
}
```

## 🔍 En Olası Sorun

**PayTR Test Mode Farkı**: 
- Local'de test mode (`PAYTR_TEST_MODE=1`) kullanılıyor ve test kartları taksit destekliyor
- Production'da production mode (`PAYTR_TEST_MODE=0`) kullanılıyor ve gerçek kart BIN'i PayTR'nin production veritabanında farklı sonuç veriyor

**Çözüm**: 
1. Production'da da test mode kullanmak için: `PAYTR_TEST_MODE=1` set edin
2. VEYA PayTR'den production hesabınızın taksit anlaşmalarını kontrol edin

## 📞 PayTR Desteği

Eğer yukarıdaki adımlar sorunu çözmezse, PayTR desteği ile iletişime geçin:
- Merchant ID'nizi verin
- Kullandığınız kart BIN'ini (ilk 6 hane) belirtin
- Test/Production mode bilgisini paylaşın
- Taksit anlaşmanızın aktif olup olmadığını sorun
