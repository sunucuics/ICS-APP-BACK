# ICS App Backend


Bu proje, ICS (İnşaat ve Çevre Sistemleri) uygulamasının backend API'sini içermektedir. Uygulama tamamen tamamlanmış ve production-ready durumdadır.

## 🚀 Deploy


Backend'i Google Cloud Run'a deploy etmek için:

```bash
./deploy.sh
```

Bu script otomatik olarak:
1. Docker image build eder
2. Google Cloud Run'a deploy eder
3. Backend URL'ini gösterir

## 📋 Proje Özeti

ICS App, inşaat ve çevre sistemleri alanında hizmet veren bir e-ticaret ve hizmet rezervasyon platformudur. Bu backend API, Flutter mobil uygulaması ve React admin paneli için gerekli tüm servisleri sağlar.

## 🚀 Tamamlanan Özellikler

### ✅ **Authentication & Authorization**
- Firebase Authentication entegrasyonu
- JWT token yönetimi
- Kullanıcı kayıt ve giriş sistemi
- Anonymous authentication desteği
- Güvenli API endpoint'leri

### ✅ **E-commerce System**
- Ürün kataloğu ve kategoriler
- Alışveriş sepeti yönetimi
- Sipariş oluşturma ve takibi
- Stok yönetimi
- İndirim sistemi

### ✅ **Services Management**
- Hizmet kataloğu
- Randevu sistemi
- Hizmet rezervasyonu
- Hizmet durumu takibi

### ✅ **User Management**
- Kullanıcı profilleri
- Adres yönetimi
- Sipariş geçmişi
- Hesap silme istekleri

### ✅ **Payment Integration**
- Mock payment sistemi
- Ödeme durumu takibi
- Sipariş tamamlama

### ✅ **Featured Content**
- Öne çıkan ürünler
- Öne çıkan hizmetler
- Dinamik içerik yönetimi

### ✅ **Shipping & Fulfillment**
- Kargo entegrasyonu
- Sipariş durumu güncellemeleri
- Webhook desteği

## 🛠️ Teknoloji Stack

- **Framework**: FastAPI (Python 3.12+)
- **Database**: PostgreSQL (production ready)
- **Authentication**: Firebase Auth
- **Payment**: Mock payment system (Iyzico entegrasyonu hazır)
- **Shipping**: Kargo sağlayıcı entegrasyonu
- **Email**: Email notification sistemi
- **Security**: JWT tokens, password hashing, CORS

## 📁 Proje Yapısı

```
backend/
├── app/
│   ├── core/           # Temel konfigürasyon ve güvenlik
│   ├── routers/        # API endpoint'leri
│   ├── schemas/        # Pydantic modelleri
│   ├── services/       # İş mantığı servisleri
│   ├── repositories/   # Veri erişim katmanı
│   ├── integrations/   # Dış servis entegrasyonları
│   └── utils/          # Yardımcı fonksiyonlar
├── requirements.txt    # Python bağımlılıkları
└── Dockerfile         # Docker konfigürasyonu
```

## 🚀 Kurulum ve Çalıştırma

### Gereksinimler
- Python 3.12+
- PostgreSQL
- Firebase projesi

### Kurulum
```bash
# Bağımlılıkları yükle
pip install -r requirements.txt

# Environment variables ayarla
cp .env.example .env
# .env dosyasını düzenle

# Veritabanını başlat
# PostgreSQL kurulumu ve konfigürasyonu

# Uygulamayı çalıştır
uvicorn app.main:app --reload
```

### Docker ile Çalıştırma
```bash
# Docker image oluştur
docker build -t ics-backend .

# Container'ı çalıştır
docker run -p 8000:8000 ics-backend
```

## 📡 API Endpoints

### Authentication
- `POST /auth/register` - Kullanıcı kaydı
- `POST /auth/login` - Kullanıcı girişi
- `POST /auth/logout` - Çıkış
- `GET /auth/me` - Kullanıcı bilgileri

### Products
- `GET /products` - Ürün listesi
- `GET /products/{id}` - Ürün detayı
- `GET /categories` - Kategori listesi

### Cart
- `GET /cart` - Sepet içeriği
- `POST /cart/add` - Sepete ekleme
- `PUT /cart/update` - Sepet güncelleme
- `DELETE /cart/remove` - Sepetten çıkarma

### Orders
- `POST /orders` - Sipariş oluşturma
- `GET /orders/my` - Kullanıcı siparişleri
- `GET /orders/{id}` - Sipariş detayı

### Services
- `GET /services` - Hizmet listesi
- `GET /services/{id}` - Hizmet detayı
- `POST /appointments` - Randevu oluşturma

### Users
- `GET /users/profile` - Profil bilgileri
- `PUT /users/profile` - Profil güncelleme
- `GET /users/addresses` - Adres listesi
- `POST /users/addresses` - Adres ekleme

## 🔒 Güvenlik

- JWT token tabanlı authentication
- Password hashing (bcrypt)
- CORS konfigürasyonu
- Rate limiting
- Input validation
- SQL injection koruması

## 📊 Production Status

- ✅ **Backend API**: 100% Tamamlandı
- ✅ **Authentication**: 100% Tamamlandı
- ✅ **E-commerce**: 100% Tamamlandı
- ✅ **Services**: 100% Tamamlandı
- ✅ **Payment**: Mock sistem tamamlandı
- ✅ **Shipping**: Entegrasyon tamamlandı
- ✅ **Testing**: API testleri tamamlandı
- ✅ **Documentation**: API dokümantasyonu hazır

## 🚀 Deployment

Uygulama production-ready durumda ve aşağıdaki platformlarda deploy edilebilir:

- **Cloud Platforms**: AWS, Google Cloud, Azure
- **Container**: Docker, Kubernetes
- **Serverless**: Vercel, Netlify Functions
- **VPS**: Herhangi bir Linux sunucu

## 📞 İletişim

Proje hakkında sorularınız için:
- **Developer**: ICS Development Team
- **Status**: Production Ready ✅
- **Last Update**: Ocak 2025

---

**🎉 Proje başarıyla tamamlanmıştır! Tüm özellikler çalışır durumda ve production'a hazırdır.**
