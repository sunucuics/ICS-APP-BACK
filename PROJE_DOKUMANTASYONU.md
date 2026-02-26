# ICS App Backend — Geliştirici Devir Dokümantasyonu

> **Son Güncelleme:** Şubat 2026  
> **Framework:** FastAPI (Python 3.11+)  
> **Veritabanı:** Firebase Firestore  
> **Auth:** Firebase Authentication  
> **Ödeme:** PayTR (iFrame + Direct API)  
> **Deploy:** Docker → Google Cloud Run

---

## 1. Proje Ne Yapar?

ICS (İnşaat ve Çevre Sistemleri) için geliştirilmiş bir **e-ticaret + hizmet randevu** platformunun backend API'sidir. İki tür istemciye hizmet verir:

| İstemci | Açıklama |
|---------|----------|
| **Flutter Mobil Uygulama** | Son kullanıcılar: ürün satın alma, sepet, sipariş, randevu alma, bildirimler |
| **React Admin Panel** | Yönetici: ürün/hizmet/sipariş/kargo yönetimi, dashboard, ayarlar |

Public endpoint'ler `/` prefix'iyle, admin endpoint'ler `/admin/` prefix'iyle ayrılır.

---

## 2. Teknoloji Stack ve Bağımlılıklar

| Kategori | Teknoloji |
|----------|-----------|
| Web Framework | FastAPI 0.111 + Uvicorn |
| Veritabanı | Google Cloud Firestore |
| Auth | Firebase Auth + JWT token doğrulama |
| Depolama | Firebase Storage (ürün/hizmet görselleri) |
| Ödeme | PayTR (iFrame token + Direct API) |
| E-posta | SMTP (Gmail, SSL/STARTTLS) |
| Push Notification | Firebase Cloud Messaging (FCM) |
| Scheduler | APScheduler (AsyncIO) |
| HTTP Client | httpx (async, connection pool, retry) |
| Cache | cachetools TTLCache (kullanıcı profili cache) |

Tam liste: `requirements.txt`

---

## 3. Proje Dizin Yapısı

```
ICS-APP-BACK/
├── .env                        # Ana ortam değişkenleri
├── Dockerfile                  # Docker build (Python 3.11-slim)
├── deploy.sh / deploy-simple.sh # Cloud Run deploy scriptleri
├── entrypoint.sh               # Docker ENTRYPOINT (uvicorn başlatır)
├── requirements.txt            # Python bağımlılıkları
├── firebase.json               # Firebase hosting config
├── firestore.indexes.json      # Firestore composite index tanımları
├── paytr.html                  # PayTR test sayfası
│
└── backend/
    ├── firebase_service_account.json  # Firebase servis hesabı
    ├── set_admin_claim.py             # Admin custom claim set scripti
    └── app/
        ├── main.py            # ⭐ FastAPI app, router kayıtları, CORS, scheduler
        ├── config.py          # ⭐ Settings (env), Firebase init, db + bucket nesneleri
        │
        ├── core/              # Çekirdek altyapı
        │   ├── auth.py        # Token decode, Principal oluşturma, dependency'ler
        │   ├── security.py    # Kullanıcı auth (cache'li), admin check, guest engelleme
        │   ├── mailer.py      # SMTP e-posta gönderici + HTML şablonları
        │   ├── http_client.py # Singleton httpx client, retry, circuit breaker
        │   ├── crypto.py      # Sayısal kod üretme, HMAC hash
        │   ├── constants.py   # Sabitler (hesap silme TTL, deneme limiti)
        │   ├── email_utils.py # E-posta gönderme helper
        │   └── deps.py        # Boş (ileride dependency injection için)
        │
        ├── routers/           # API endpoint dosyaları (20 dosya)
        │   ├── auth.py           # Kayıt, giriş, çıkış, şifre sıfırlama, profil güncelleme
        │   ├── auth_delete.py    # Hesap silme (kod doğrulamalı)
        │   ├── users.py          # Profil, adres CRUD, FCM token, admin kullanıcı yönetimi
        │   ├── products.py       # Ürün listele/detay + admin CRUD + indirim hesaplama
        │   ├── categories.py     # Kategori listele + admin CRUD + pin/unpin
        │   ├── services.py       # Hizmet listele + admin CRUD + görsel yönetimi
        │   ├── carts.py          # Sepet: ekle/çıkar/temizle/toplam
        │   ├── shipping_manual.py # ⭐ Sipariş oluşturma, listeleme, kargo lifecycle
        │   ├── appointments.py   # ⭐ Randevu talebi, takvim, admin onay/iptal
        │   ├── discounts.py      # Admin: indirim CRUD + fiyat recalc
        │   ├── comments.py       # Yorum CRUD + küfür filtresi + admin onay
        │   ├── featured.py       # Öne çıkan ürün/hizmet yönetimi
        │   ├── notifications.py  # Admin: bildirim şablonları, kampanyalar, push gönderme
        │   ├── admin_notifications.py # Admin panel bildirimleri + SSE stream
        │   ├── user_notifications.py  # Kullanıcı bildirimleri
        │   ├── admin_dashboard.py     # Dashboard istatistikleri
        │   ├── analytics.py          # Satış/gelir/ürün analitiği
        │   ├── settings.py           # Uygulama ayarları, e-posta şablonları, yedekleme
        │   ├── paytr.py              # PayTR iFrame token + callback + taksit hesaplama
        │   └── shipping.py           # Boş/minimal (shipping_manual aktif)
        │
        ├── payments/
        │   └── paytr_direct.py   # ⭐ PayTR Direct API (token, BIN, taksit, callback, sipariş)
        │
        ├── schemas/           # Pydantic modelleri (request/response)
        │   ├── user.py, product.py, service.py, cart.py, category.py
        │   ├── appointment.py, comment.py, discount.py, featured.py
        │   ├── notification.py, admin_notification.py, order_schemas.py
        │   ├── pagination.py, principal.py, delete.py, settings.py
        │   └── __init__.py
        │
        ├── services/          # İş mantığı katmanı
        │   ├── featured_service.py  # Öne çıkan ürün/hizmet CRUD (Firestore)
        │   └── account_delete.py    # Hesap silme akışı (kod üret → doğrula → sil)
        │
        ├── repositories/      # Veri erişim katmanı
        │   └── delete_requests.py   # Hesap silme istekleri (Firestore CRUD)
        │
        ├── model/
        │   └── order.py       # Sipariş modeli (OrderStatus enum)
        │
        ├── utils/
        │   ├── ip.py          # Client IP tespiti (proxy-aware, XFF)
        │   └── categories.py  # Kategori yardımcı fonksiyonları
        │
        └── templates/
            └── paytr_direct_form.html  # PayTR ödeme formu HTML
```

---

## 4. Dosya-Fonksiyon Haritası

### 4.1 `main.py` — Uygulama Giriş Noktası

| Fonksiyon | Satır | Açıklama |
|-----------|-------|----------|
| `paytr_direct_form()` | 39-42 | PayTR ödeme formu HTML sayfası (GET /paytr/direct/form) |
| `_startup_scheduler()` | 99-103 | Uygulama başlarken APScheduler'ı başlatır |
| `_shutdown_scheduler()` | 106-115 | Uygulama kapanırken scheduler + HTTP client kapatır |
| `healthz()` | 118-126 | Liveness check (GET /healthz) |
| `readyz()` | 130-132 | Readiness check (GET /readyz) |

**Router Kayıt Sırası:** Public router'lar doğrudan, admin router'lar `/admin` prefix'i ile eklenir.

---

### 4.2 `config.py` — Konfigürasyon ve Firebase Init

| Öğe | Açıklama |
|-----|----------|
| `Settings` class | Pydantic BaseSettings: tüm env değişkenlerini yükler |
| `settings` | Global singleton Settings instance |
| `db` | Firestore client (tüm modüller bunu import eder) |
| `bucket` | Firebase Storage bucket |
| Firebase init bloğu | Env'den veya JSON dosyadan credential yükler |

---

### 4.3 `core/auth.py` — Token Doğrulama ve Principal

| Fonksiyon | Açıklama |
|-----------|----------|
| `_extract_bearer_token(request)` | Authorization header'dan token çıkarır |
| `_decode_id_token(id_token, check_revoked)` | Firebase ID token doğrular (mock token desteği ile) |
| `_decode_mock_token(mock_token)` | Development için mock token decode |
| `_token_to_principal(decoded)` | Token → Principal dönüşümü (guest/user/admin rolü) |
| `get_optional_principal(request)` | Token opsiyonel dependency |
| `get_principal(request)` | Token zorunlu dependency (herkes) |
| `get_current_user(request)` | Token zorunlu, guest hariç |
| `get_current_admin(request)` | Token zorunlu, sadece admin |

---

### 4.4 `core/security.py` — Güvenlik Katmanı (Eski Stil + Yeni Stil)

| Fonksiyon | Açıklama |
|-----------|----------|
| `invalidate_user_cache(uid)` | Kullanıcı cache'ini temizler |
| `get_current_user(credentials)` | Token doğrula + Firestore profil getir (TTL cache'li) |
| `get_current_user_strict(credentials)` | check_revoked=True ile hassas doğrulama |
| `get_current_admin(current_user)` | Admin kontrolü (eski stil, dict-based) |
| `require_non_guest(principal)` | Guest kullanıcıları 403 ile engeller |
| `require_admin(principal)` | Sadece admin kabul eder |

> **Not:** Projede iki auth sistemi paralel çalışır: eski (`security.py` → dict döner) ve yeni (`auth.py` → `Principal` döner). Bazı router'lar `security.get_current_user`'ı, bazıları `auth.get_principal`'ı kullanır.

---

### 4.5 `core/mailer.py` — E-posta Gönderimi

| Fonksiyon | Açıklama |
|-----------|----------|
| `mailer_send(to, subject, html, ...)` | SMTP ile e-posta gönderir (SSL/STARTTLS, otomatik fallback) |
| `tpl_shipped_html(...)` | "Kargoya verildi" e-posta şablonu |
| `tpl_delivered_html(...)` | "Teslim edildi" e-posta şablonu |
| `tpl_canceled_html(...)` | "İptal edildi" e-posta şablonu |
| `_items_block(items, totals)` | Sipariş kalemleri HTML bloğu |
| `_address_block(address)` | Adres HTML bloğu |

---

### 4.6 `core/http_client.py` — HTTP İstemci Altyapısı

| Fonksiyon | Açıklama |
|-----------|----------|
| `get_http_client()` | Global singleton httpx.AsyncClient döndürür |
| `close_http_client()` | Client'ı kapatır (shutdown'da çağrılır) |
| `retry_with_backoff(...)` | Exponential backoff retry decorator |
| `safe_firebase_request(method, url)` | Firebase API'ye güvenli istek (retry + circuit breaker) |

---

### 4.7 `routers/auth.py` — Kimlik Doğrulama

| Endpoint | Metod | Fonksiyon | Açıklama |
|----------|-------|-----------|----------|
| `/auth/register` | POST | `register()` | Kullanıcı kaydı (form-data, Firebase Auth + Firestore profil) |
| `/auth/login` | POST | `login()` | Firebase REST API ile giriş, id_token + refresh_token döner |
| `/auth/logout` | POST | `logout()` | Tüm refresh token'ları iptal eder |
| `/auth/refresh-token` | POST | `refresh_token()` | Refresh token ile yeni ID token alır |
| `/auth/password-reset` | GET | `request_password_reset()` | Şifre sıfırlama e-postası gönderir |
| `/auth/profile` | PUT | `update_my_profile()` | Profil bilgilerini günceller |

---

### 4.8 `routers/auth_delete.py` — Hesap Silme

| Endpoint | Metod | Fonksiyon | Açıklama |
|----------|-------|-----------|----------|
| `/auth/delete-account/initiate` | POST | `initiate_delete_account()` | Doğrulama kodu e-posta ile gönderir |
| `/auth/delete-account/verify` | POST | `verify_delete_account()` | Kodu doğrular, hesabı kalıcı olarak siler |

---

### 4.9 `routers/users.py` — Kullanıcı ve Adres Yönetimi

| Endpoint | Metod | Fonksiyon | Açıklama |
|----------|-------|-----------|----------|
| `/users/me` | GET | `get_my_profile()` | Profil bilgileri |
| `/users/me/addresses` | POST | `add_address()` | Yeni adres ekle |
| `/users/me/addresses/{id}` | PUT | `update_address()` | Adres güncelle |
| `/users/me/addresses/{id}` | DELETE | `delete_address()` | Adres sil |
| `/users/me/addresses` | GET | `list_addresses()` | Tüm adresler |
| `/users/me/addresses/current` | GET | `get_current_address()` | Varsayılan adres |
| `/users/me/addresses/{id}/choose` | PUT | `choose_current_address()` | Varsayılan adres seç |
| `/users/me/fcm-token` | PUT | `update_fcm_token()` | FCM token güncelle |
| **Admin:** `/admin/users/` | GET | `list_users()` | Tüm kullanıcıları listele |
| **Admin:** `/admin/users/{id}` | GET | `get_user_by_id()` | Kullanıcı detayı |
| **Admin:** `/admin/users/{id}/role` | PUT | `update_user_role()` | Rol değiştir |
| **Admin:** `/admin/users/{id}` | DELETE | `delete_user()` | Kullanıcı sil |

---

### 4.10 `routers/products.py` — Ürün Yönetimi

| Endpoint | Metod | Fonksiyon | Açıklama |
|----------|-------|-----------|----------|
| `/products` | GET | `list_products_no_slash()` | Ürünleri listele (cursor-based pagination, kategori filtresi) |
| `/products/{id}` | GET | `get_product()` | Ürün detayı (indirimli fiyat hesaplı) |
| **Admin:** `/admin/products/` | POST | `create_product()` | Ürün oluştur (form + fotoğraf upload) |
| **Admin:** `/admin/products/json` | POST | `create_product_json()` | Ürün oluştur (JSON, fotoğrafsız) |
| **Admin:** `/admin/products/{id}` | DELETE | Silme (soft/hard) |
| **Admin:** `/admin/products/fix-prices` | PUT | `fix_prices_kurus_to_tl()` | Kuruş→TL fiyat düzeltme |
| **Admin:** `/admin/products/check-prices` | GET | `check_prices()` | Fiyat tutarsızlığı tespiti |

**Yardımcı Fonksiyonlar:**

| Fonksiyon | Açıklama |
|-----------|----------|
| `_load_all_active_discounts()` | Tüm aktif indirimleri toplu yükler (N+1 sorgu çözümü) |
| `_calculate_final_price_from_cache()` | Cache'den indirimli fiyat hesaplar |
| `_calculate_final_price()` | Tek ürün için indirimli fiyat (detay sayfası) |
| `_delete_discounts_for_product()` | Ürüne bağlı indirimleri temizler |
| `_list_products_impl()` | Cursor-based pagination ile ürün listesi |

---

### 4.11 `routers/categories.py` — Kategori Yönetimi

| Endpoint | Metod | Fonksiyon | Açıklama |
|----------|-------|-----------|----------|
| `/categories` | GET | `list_categories_no_slash()` | Kategorileri listele (pin'li ilk sırada) |
| **Admin:** `/admin/categories/` | POST | `create_category()` | Kategori oluştur (kapak görseli ile) |
| **Admin:** `/admin/categories/{id}` | PUT | `update_category()` | Kategori güncelle |
| **Admin:** `/admin/categories/{id}` | DELETE | `delete_category()` | Kategori sil (soft/hard) |
| **Admin:** `/admin/categories/{id}/pin` | PUT | `pin_category()` | Kategori sabitle |
| **Admin:** `/admin/categories/{id}/unpin` | PUT | `unpin_category()` | Sabitleme kaldır |

---

### 4.12 `routers/services.py` — Hizmet Yönetimi

| Endpoint | Metod | Fonksiyon | Açıklama |
|----------|-------|-----------|----------|
| `/services` | GET | `list_services_no_slash()` | Hizmetleri listele |
| **Admin:** `/admin/services/` | POST | `create_service()` | Hizmet oluştur (3 görsel slot) |
| **Admin:** `/admin/services/{id}` | PUT | `update_service()` | Hizmet güncelle (görsel değiştir/sil) |
| **Admin:** `/admin/services/{id}` | DELETE | `delete_service()` | Hizmet sil |
| **Admin:** `/admin/services/{id}/images` | DELETE | `delete_service_image()` | Tek görsel sil (index ile) |

---

### 4.13 `routers/carts.py` — Sepet

| Endpoint | Metod | Fonksiyon | Açıklama |
|----------|-------|-----------|----------|
| `/cart/add` | POST | `add_to_cart()` | Sepete ürün ekle (ID + miktar) |
| `/cart/remove/{product_id}` | DELETE | `remove_cart_item()` | Sepetten ürün çıkar |
| `/cart/clear` | DELETE | `clear_cart()` | Sepeti temizle |
| `/cart` | GET | `get_cart_no_slash()` | Sepet detayı (ürün bilgileri ile) |
| `/cart/total` | GET | `cart_total()` | Sepet toplamı |

**Not:** Sepet Firestore `carts/{uid}` koleksiyonunda saklanır. Ürün bilgileri `collection_group("items")` ile çekilir.

---

### 4.14 `routers/shipping_manual.py` — Sipariş ve Kargo ⭐

Bu dosya projenin en büyük router'ıdır (948 satır). Sipariş yaşam döngüsünün tamamını yönetir.

| Endpoint | Metod | Fonksiyon | Açıklama |
|----------|-------|-----------|----------|
| `/orders` | POST | `create_order()` | Sepetten sipariş oluştur (stok düş, transaction) |
| `/orders/my` | GET | `list_my_orders()` | Kullanıcının siparişleri (active/past) |
| `/orders/{id}` | GET | `get_order_public()` | Sipariş detayı |
| `/orders/{id}/cancel` | POST | `cancel_awaiting_order()` | Ödeme bekleyen siparişi iptal et |
| **Admin:** `/admin/orders/ship-queue` | GET | `list_ship_queue()` | Kargoya verilecek siparişler |
| **Admin:** `/admin/orders/{id}/ship` | PUT | Kargoya ver (tracking no ekle) |
| **Admin:** `/admin/orders/{id}/deliver` | PUT | Teslim edildi olarak işaretle |
| **Admin:** `/admin/orders/{id}/cancel` | PUT | Admin sipariş iptali (stok geri yükle) |

**Sipariş Durumları:** `awaiting_payment` → `paid` → `processing` → `shipped` → `delivered` (veya `cancelled`)

**Önemli Yardımcı Fonksiyonlar:**

| Fonksiyon | Açıklama |
|-----------|----------|
| `_find_product_refs()` | Sipariş kalemleri için ürün referanslarını bulur |
| `_restore_stock()` | İptal edilen siparişin stoğunu geri yükler |
| `_load_cart_items()` | Kullanıcının sepetini ürün bilgileri ile yükler |
| `_calc_totals()` | Sipariş toplamlarını hesaplar |
| `_build_customer()` | Firestore'dan müşteri bilgilerini getirir |
| `_ensure_transition()` | Sipariş durumu geçişini doğrular |
| `_customer_email_from_order()` | Siparişten müşteri e-postasını çıkarır |

---

### 4.15 `routers/appointments.py` — Randevu Sistemi ⭐

| Endpoint | Metod | Fonksiyon | Açıklama |
|----------|-------|-----------|----------|
| `/appointments` | POST | `request_appointment()` | Randevu talebi (form-data) |
| `/appointments/my` | GET | `list_my_appointments()` | Kullanıcının randevuları |
| **Admin:** `/admin/appointments/` | GET | `list_appointments_no_slash()` | Tüm randevular (status filtresi) |
| **Admin:** `/admin/appointments/` | POST | `create_appointment()` | Elle randevu oluştur |
| **Admin:** `/admin/appointments/block-day` | POST | `block_entire_day()` | Günü tamamen blokla |
| **Admin:** `/admin/appointments/{id}/status` | PUT | `update_appointment_status_form()` | Durum güncelle |
| **Admin:** `/admin/appointments/{id}` | DELETE | `delete_appointment()` | Randevu sil |
| **Admin:** Müsaitlik | GET/PUT | `get/update_service_availability()` | Çalışma saatleri |

**Tarih İşleme:** Tüm tarihler UTC olarak saklanır, API yanıtlarında Türkiye saatine (UTC+3) çevrilir.

| Fonksiyon | Açıklama |
|-----------|----------|
| `_coerce_dt()` | Çeşitli tarih formatlarını naive UTC'ye dönüştürür |
| `_to_local_dt()` | UTC → Türkiye saati (API response) |
| `_from_local_dt()` | Türkiye saati → UTC (kaydetme) |
| `_send_admin_push_notification()` | Admin'lere FCM push bildirim gönderir |
| `_notify_user_status_change()` | Kullanıcıya bildirim + FCM push (background thread) |

---

### 4.16 `routers/discounts.py` — İndirim Yönetimi (Admin)

| Endpoint | Metod | Fonksiyon | Açıklama |
|----------|-------|-----------|----------|
| `/admin/discounts/` | GET | `list_discounts()` | İndirimleri listele |
| `/admin/discounts/{id}` | GET | `get_discount()` | İndirim detayı |
| `/admin/discounts/` | POST | `create_discount_json_no_slash()` | JSON ile indirim oluştur |
| `/admin/discounts/product` | POST | `create_discount_product()` | Form ile ürün indirimi |
| `/admin/discounts/{id}` | PUT | `update_discount_json()` | JSON ile güncelle |
| `/admin/discounts/{id}` | DELETE | `delete_discount()` | İndirim sil + fiyat recalc |

**Not:** İndirim oluşturma/güncelleme/silme sonrasında ilgili ürünlerin `final_price` alanı otomatik yeniden hesaplanır.

---

### 4.17 `routers/comments.py` — Yorum Sistemi

| Endpoint | Metod | Fonksiyon | Açıklama |
|----------|-------|-----------|----------|
| `/comments/products/{id}` | POST | `create_comment_for_product()` | Ürüne yorum yaz |
| `/comments/services/{id}` | POST | `create_comment_for_service()` | Hizmete yorum yaz |
| `/comments/products/{id}` | GET | `list_product_comments()` | Ürün yorumları |
| `/comments/services/{id}` | GET | `list_service_comments()` | Hizmet yorumları |
| **Admin:** `/admin/comments/` | GET | `list_all_comments()` | Tüm yorumlar (user + target isimleri ile) |
| **Admin:** `/admin/comments/{id}` | DELETE | `admin_delete_comment()` | Yorum sil (soft/hard) |
| **Admin:** `/admin/comments/{id}/approve` | PUT | `admin_approve_comment()` | Yorumu onayla |
| **Admin:** `/admin/comments/profanity/` | GET/POST/DELETE | Küfür listesi yönetimi |

**Küfür Filtresi:** `settings/profanity` Firestore dokümanında `blocked_words` listesi tutulur.

---

### 4.18 `routers/featured.py` — Öne Çıkan İçerikler (Admin)

| Endpoint | Metod | Fonksiyon | Açıklama |
|----------|-------|-----------|----------|
| `/admin/featured/products/{id}` | POST | `feature_product()` | Ürünü öne çıkar |
| `/admin/featured/products/{id}` | DELETE | `unfeature_product()` | Öne çıkarmayı kaldır |
| `/admin/featured/products` | GET | `list_featured_products()` | Öne çıkan ürünler |
| `/admin/featured/services/{id}` | POST | `feature_service()` | Hizmeti öne çıkar |
| `/admin/featured/services/{id}` | DELETE | `unfeature_service()` | Öne çıkarmayı kaldır |
| `/admin/featured/services` | GET | `list_featured_services()` | Öne çıkan hizmetler |

---

### 4.19 `routers/notifications.py` — Bildirim Şablonları ve Kampanyalar (Admin)

| Endpoint | Metod | Fonksiyon | Açıklama |
|----------|-------|-----------|----------|
| `/admin/notifications/templates` | GET/POST | Template CRUD | Bildirim şablonları |
| `/admin/notifications/templates/{id}` | PUT/DELETE | Template güncelle/sil |
| `/admin/notifications/campaigns` | GET/POST | Kampanya CRUD |
| `/admin/notifications/campaigns/{id}` | PUT/DELETE | Kampanya güncelle/sil |
| `/admin/notifications/send` | POST | `send_notification()` | Push bildirim gönder (FCM) |

---

### 4.20 `routers/admin_notifications.py` — Admin Panel Bildirimleri

| Endpoint | Metod | Fonksiyon | Açıklama |
|----------|-------|-----------|----------|
| `/admin/notifications/` | GET | `get_admin_notifications()` | Admin bildirimlerini listele |
| `/admin/notifications/unread-count` | GET | `get_unread_count()` | Okunmamış sayısı |
| `/admin/notifications/{id}/read` | PUT | `mark_notification_as_read()` | Okundu işaretle |
| `/admin/notifications/read-all` | PUT | `mark_all_notifications_as_read()` | Hepsini okundu yap |
| `/admin/notifications/stream` | GET | `stream_admin_notifications()` | **SSE** (Server-Sent Events) gerçek zamanlı bildirim stream'i |

**SSE:** Firestore `on_snapshot` listener → `asyncio.Queue` → SSE event generator. Gerçek zamanlı bildirim akışı sağlar.

---

### 4.21 `routers/user_notifications.py` — Kullanıcı Bildirimleri

| Endpoint | Metod | Fonksiyon | Açıklama |
|----------|-------|-----------|----------|
| `/notifications` | GET | `get_notifications()` | Kullanıcının bildirimleri |
| `/notifications/{id}` | GET | `get_notification()` | Bildirim detayı |
| `/notifications/{id}/read` | PUT | `mark_notification_as_read()` | Okundu işaretle |
| `/notifications/read-all` | PUT | `mark_all_notifications_as_read()` | Hepsini okundu yap |
| `/notifications/{id}` | DELETE | `delete_notification()` | Bildirim sil |

---

### 4.22 `routers/admin_dashboard.py` — Dashboard İstatistikleri (Admin)

| Endpoint | Metod | Fonksiyon | Açıklama |
|----------|-------|-----------|----------|
| `/admin/dashboard/stats` | GET | `get_dashboard_stats()` | Detaylı istatistikler (paralel thread) |
| `/admin/dashboard/overview` | GET | `get_dashboard_overview()` | Genel özet |

**Optimizasyon:** 6 veri çekici paralel thread'lerde çalışır. Firestore aggregation count kullanılır.

---

### 4.23 `routers/analytics.py` — Analitik (Admin)

| Endpoint | Metod | Fonksiyon | Açıklama |
|----------|-------|-----------|----------|
| `/admin/analytics/` | GET | `get_analytics_data()` | Kapsamlı analitik (dönem bazlı) |
| `/admin/analytics/overview` | GET | `get_analytics_overview()` | Analitik özeti |
| `/admin/analytics/revenue` | GET | `get_revenue_analytics()` | Gelir analizi (12 aylık) |
| `/admin/analytics/products` | GET | `get_product_analytics()` | En çok satan ürünler |

---

### 4.24 `routers/settings.py` — Uygulama Ayarları (Admin)

| Endpoint | Metod | Fonksiyon | Açıklama |
|----------|-------|-----------|----------|
| `/admin/settings/` | GET | `get_settings_data()` | Tüm ayarları getir |
| `/admin/settings/app` | GET/PUT | Uygulama ayarları |
| `/admin/settings/email-templates` | GET/POST | E-posta şablonları |
| `/admin/settings/email-templates/{id}` | PUT/DELETE | Şablon güncelle/sil |
| `/admin/settings/backup` | GET/PUT | Yedekleme ayarları |

---

### 4.25 `routers/paytr.py` — PayTR iFrame Entegrasyonu

| Endpoint | Metod | Fonksiyon | Açıklama |
|----------|-------|-----------|----------|
| `/paytr/token` | POST | `create_token()` | iFrame token oluştur |
| `/paytr/callback` | POST | `paytr_callback()` | PayTR IPN callback (ödeme sonucu) |
| `/paytr/installment/quote` | GET | `installment_quote()` | Taksit hesaplama (banka bazlı) |

---

### 4.26 `payments/paytr_direct.py` — PayTR Direct API ⭐

En büyük dosya (1046 satır). Doğrudan kart bilgisi ile ödeme işleme.

| Endpoint | Metod | Fonksiyon | Açıklama |
|----------|-------|-----------|----------|
| `/paytr/direct/init` | POST | Direct payment başlat |
| `/paytr/direct/callback` | POST | Direct payment callback |
| `/paytr/bin-detail` | POST | Kart BIN sorgula |
| `/paytr/installment-rates` | POST | Taksit oranları |
| `/paytr/installment-for-bin` | POST | Karta özel taksit |
| `/paytr/iframe/init` | POST | iFrame ile ödeme başlat |
| `/paytr/iframe/callback` | POST | iFrame callback |
| `/paytr/status/{merchant_oid}` | GET | Ödeme durumu sorgula |

**Önemli Fonksiyonlar:**

| Fonksiyon | Açıklama |
|-----------|----------|
| `_calc_direct_token()` | Direct API için HMAC token hesaplar |
| `_calc_iframe_token()` | iFrame API için HMAC token hesaplar |
| `normalize_and_validate_client_amount()` | TL/kuruş normalleştirme + manipülasyon kontrolü |
| `apply_installment_rate()` | Taksit oranı uygulama |
| `paytr_bin_detail()` | Kart BIN bilgisi sorgulama |
| `paytr_installment_rates()` | Taksit oranlarını çekme |

---

### 4.27 `services/account_delete.py` — Hesap Silme Servisi

| Fonksiyon | Açıklama |
|-----------|----------|
| `initiate(uid, email, display_name)` | Doğrulama kodu üretir, hash'ler, Firestore'a yazar, e-posta gönderir |
| `verify_and_delete(uid, code)` | Kodu doğrular → tüm kullanıcı verilerini + Auth hesabını siler |
| `_cleanup_user_data(uid)` | İlişkili koleksiyonları temizler (addresses, orders, notifications) |
| `_revoke_and_delete_user(uid)` | Refresh token iptal + Firestore profil sil + Firebase Auth sil |

---

### 4.28 `services/featured_service.py` — Öne Çıkan İçerik Servisi

| Fonksiyon | Açıklama |
|-----------|----------|
| `feature(kind, item_id, admin_uid)` | Ürün/hizmet öne çıkar (idempotent) |
| `unfeature(kind, item_id)` | Öne çıkarmayı kaldır |
| `list_items(kind, expand_detail)` | Öne çıkan öğeleri listele (detaylı/özet) |
| `detail_of(kind, item_id)` | Kaynak dokümanı getir |
| `_find_source_snap(kind, item_id)` | Collection group + fallback ile kaynak bul |

---

### 4.29 `utils/ip.py` — IP Tespiti

| Fonksiyon | Açıklama |
|-----------|----------|
| `get_client_public_ip(request, override)` | Proxy-aware public IP tespiti |
| `_iter_candidate_ips()` | Aday IP'leri öncelik sırasıyla döner |
| `_is_private_or_local()` | Private/loopback IP kontrolü |

---

## 5. Firestore Koleksiyon Yapısı

| Koleksiyon | Açıklama |
|------------|----------|
| `users` | Kullanıcı profilleri (name, email, phone, addresses[], role, fcm_token) |
| `categories` | Ürün kategorileri (name, cover_image, is_fixed, is_deleted) |
| `categories/{catId}/items` | Ürünler (alt koleksiyon: title, price, final_price, images[], stock) |
| `services` | Hizmetler (title, description, images[], is_upcoming, is_deleted) |
| `carts` | Sepetler (items map: product_id → quantity) |
| `orders` | Siparişler (items[], totals, status, customer, tracking_number) |
| `appointments` | Randevular (service_id, user_id, start, end, status) |
| `comments` | Yorumlar (target_type, target_id, user_id, content, rating) |
| `discounts` | İndirimler (target_type, target_id, percentage, start_at, end_at) |
| `featured_products` | Öne çıkan ürünler (item_id → created_by, created_at) |
| `featured_services` | Öne çıkan hizmetler |
| `admin_notifications` | Admin panel bildirimleri |
| `user_notifications` | Kullanıcı bildirimleri |
| `notification_templates` | Bildirim şablonları |
| `notification_campaigns` | Bildirim kampanyaları |
| `payment_sessions` | Ödeme oturumları (PayTR) |
| `delete_requests` | Hesap silme istekleri (code_hash, expires_at, attempts) |
| `settings/profanity` | Küfür filtresi ayarları |
| `settings/app` | Uygulama genel ayarları |

> **Ürün Yapısı:** Ürünler `categories/{catId}/items/{itemId}` şeklinde nested koleksiyondadır. Listelerken `collection_group("items")` kullanılır. Her üründe `id` alanı kendi doc ID'sine eşittir.

---

## 6. Ortam Değişkenleri (Environment Variables)

`.env.example` dosyasına bakın. Kritik değişkenler:

| Değişken | Zorunlu | Açıklama |
|----------|---------|----------|
| `FIREBASE_PROJECT_ID` | ✅ | Firebase proje ID |
| `FIREBASE_STORAGE_BUCKET` | ✅ | Storage bucket adı |
| `FIREBASE_WEB_API_KEY` | ✅ | Firebase Web API key (AIza ile başlamalı) |
| `FIREBASE_CRED_FILE` | ❌ | Servis hesabı JSON dosya yolu (default: firebase_service_account.json) |
| `PAYTR_MERCHANT_ID` | ✅ | PayTR merchant ID |
| `PAYTR_MERCHANT_KEY` | ✅ | PayTR merchant key |
| `PAYTR_MERCHANT_SALT` | ✅ | PayTR merchant salt |
| `PAYTR_TEST_MODE` | ❌ | "1" = test modu aktif |
| `SMTP_HOST` | ✅ | SMTP sunucu (ör: smtp.gmail.com) |
| `SMTP_PORT` | ✅ | SMTP port (465=SSL, 587=STARTTLS) |
| `SMTP_USER` | ✅ | SMTP kullanıcı |
| `SMTP_PASSWORD` | ✅ | SMTP şifre (app password) |
| `ALLOWED_ORIGINS` | ❌ | CORS izinli origin'ler (virgülle ayrılmış) |
| `DEBUG` | ❌ | true = debug modu (mock token aktif) |

---

## 7. Kurulum Adımları (Sıfırdan)

```bash
# 1. Repo'yu klonla
git clone <repo-url> && cd ICS-APP-BACK

# 2. Virtual environment oluştur
python3 -m venv venv
source venv/bin/activate

# 3. Bağımlılıkları yükle
pip install -r requirements.txt

# 4. Environment ayarla
cp backend/app/.env.example backend/app/.env
# .env dosyasını düzenle (Firebase, PayTR, SMTP bilgileri)

# 5. Firebase servis hesabı JSON dosyasını koy
# firebase_service_account.json dosyasını proje kök dizinine koy

# 6. Çalıştır
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000

# 7. Swagger UI
# http://localhost:8000/docs
```

---

## 8. Docker ile Deploy

```bash
# Build
docker build -t ics-backend .

# Lokal çalıştırma
docker run -p 8080:8080 --env-file .env ics-backend

# Google Cloud Run'a deploy
./deploy.sh
```

**Dockerfile özeti:** Python 3.11-slim → pip install → backend/ kopyala → appuser ile çalıştır → `entrypoint.sh` (uvicorn)

---

## 9. Firestore Index'leri

`firestore.indexes.json` dosyasında tanımlı composite index'ler:

- `appointments`: status + start (randevu sıralama)
- `categories`: is_deleted + created_at (kategori listesi)
- `comments`: çeşitli filtre kombinasyonları (target, type, hidden, deleted)
- `items` (ürünler): is_deleted + created_at + category_name (ürün listesi)
- `orders`: user_id/status/is_deleted + created_at (sipariş listesi)
- `services`: is_deleted + created_at (hizmet listesi)

Index'leri deploy etmek için:
```bash
firebase deploy --only firestore:indexes
```

---

## 10. Dikkat Edilmesi Gereken Noktalar

### ⚠️ İki Auth Sistemi Paralel Çalışıyor
- **Eski:** `core/security.py` → `get_current_user()` → `dict` döner
- **Yeni:** `core/auth.py` → `get_principal()` → `Principal` döner
- Bazı router'lar eski sistemi, bazıları yeni sistemi kullanıyor. Yeni geliştirmelerde `auth.py` tercih edin.

### ⚠️ Ürün Yapısı Nested
- Ürünler `categories/{catId}/items/{itemId}` altında. Tek ürün aramak için `collection_group("items")` kullanılır.
- Bu yapı composite index gerektirir (`firestore.indexes.json`).

### ⚠️ PayTR İki Farklı Modül
- `routers/paytr.py` → iFrame tabanlı ödeme (eski/yedek)
- `payments/paytr_direct.py` → Direct API ödeme (aktif)
- `main.py`'de `paytr.router` **yoruma alınmış** durumda, `paytr_direct_router` aktif.

### ⚠️ Tarih İşleme
- Tüm tarihler Firestore'da **UTC** olarak saklanır.
- API yanıtlarında **Türkiye saatine (UTC+3)** çevrilir.
- `_coerce_dt()`, `_to_local_dt()`, `_from_local_dt()` fonksiyonları bu dönüşümü yapar.

### ⚠️ Trailing Slash
- FastAPI `redirect_slashes=False` ile çalışıyor.
- Bu yüzden birçok endpoint hem slash'li hem slash'siz tanımlanmış (ör: `list_products_no_slash` + `list_products_with_slash`).

### ⚠️ Collection Prefix
- Bazı modüller `FIREBASE_COLLECTION_PREFIX` env değişkenini destekler (carts, orders, payment_sessions).
- Bu, aynı Firestore'da birden fazla ortam çalıştırmak için kullanılabilir.

---

## 11. Admin Custom Claim Ayarlama

Admin kullanıcı oluşturmak için `backend/set_admin_claim.py` scripti kullanılır. Bu script Firebase Auth'da kullanıcıya `admin=True` custom claim ekler.

---

## 12. Test

```bash
# Test çalıştırma
pytest backend/app/

# PayTR BIN testi
./test_bin_production.sh
```

---

> **Bu doküman projenin Şubat 2026 itibariyle son halini yansıtmaktadır.**
