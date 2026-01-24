# ICS Back-End Ödeme ve Sipariş Sistemi Entegrasyon Dokümantasyonu

Bu belge, Frontend ekibi için **Sipariş Oluşturma (Order Creation)** ve **PayTR Ödeme (Payment)** süreçlerinin teknik detaylarını, veri yapılarını ve akış diyagramlarını içerir.

## 1. Mimari Genel Bakış ("Session-First" Yaklaşımı)

Sistemimiz **"Session-First"** (Önce Oturum, Sonra Sipariş) mantığıyla çalışmaktadır. Bu, ödeme işlemi tamamlanana kadar veritabanında "Orders" (Siparişler) tablosuna kayıt atılmadığı anlamına gelir.

**Neden?** : Başarısız ödemelerin sipariş tablosunu kirletmesini engellemek ve veri tutarlılığını sağlamak için.

### Temel Akışlar
1.  **Dahil (PayTR):** Kullanıcı sepeti onaylar -> Backend "Ödeme Oturumu" başlatır -> Ödeme Başarılı Olursa -> Backend otomatik olarak Siparişi oluşturur ve Sepeti temizler.
2.  **Manuel (Havale/EFT):** Kullanıcı doğrudan siparişi onaylar -> Backend anında Siparişi oluşturur ve Sepeti temizler.

---

## 2. PayTR ile Kredi Kartı Ödemesi (PayTR Direct API)

Bu akışta frontend, kullanıcıyı PayTR sayfasına yönlendirmez; backend'den aldığı token ile **kendi arayüzünde (veya hidden form ile)** ödemeyi başlatır.

### Adım 1: Ödeme Oturumu Başlatma (Init)
Kullanıcı "Ödeme Yap" dediğinde çağrılacak endpoint.

*   **Endpoint:** `POST /paytr/direct/init`
*   **Amaç:** Sepet tutarını hesaplamak, taksit seçeneklerini uygulamak ve PayTR Token'ı almak.

**Request Body (JSON):**
```json
{
  "merchant_oid": "BENZERSIZ_SIPARIS_NO_URETIN",  // Frontend tarafında UUID/Random string üretilmeli
  "email": "user@example.com",
  "payment_amount": 0,          // Backend sepeti baz alır, burası 0 gönderilebilir (backend hesaplar)
  "payment_type": "card",       // Sabit
  "installment_count": 0,       // Peşin için 0, Taksit için 3 (Sadece 0 ve 3 desteklenir)
  "currency": "TL",
  "non_3d": 0,
  "client_lang": "tr",
  "user_name": "Ad Soyad",
  "user_address": "Tam Açık Adres Stringi",
  "user_phone": "0555xxxxxxx",
  "basket": [                   // Sepetteki ürünlerin listesi
    {
      "name": "Ürün Adı",
      "price": 100.50,          // Birim Fiyat (Float)
      "quantity": 2             // Adet (Int)
    }
  ],
  "bin_number": "454360",       // OPSİYONEL: 3 Taksit ise ZORUNLU (Kartın ilk 6 hanesi)
  "user_ip": "1.1.1.1",         // Kullanıcının IP adresi
  "debug_on": 1
}
```

**Response Body (JSON):**
Bu yanıt geldiğinde henüz sipariş oluşmamıştır. `fields` içindeki verilerle bir HTML Form post edilmelidir.

```json
{
  "action": "https://www.paytr.com/odeme",
  "fields": {
    "merchant_id": "xxxxxx",
    "merchant_oid": "GONDERDIGINIZ_OID",
    "paytr_token": "TOKEN_STRING_BURADA",
    "payment_amount": "100.50",   // Backend tarafından hesaplanmış nihai tutar
    "user_basket": "BASE64...",
    "card_type": "maximum",       // Sadece taksitte döner (Marka bilgisi)
    ... // Diğer gerekli hidden fieldlar
  }
}
```

---

### Adım 2: Ödeme Formunu Post Etme
Backend'den gelen `fields` objesindeki her bir key-value çifti için `<input type="hidden">` oluşturun. Ayrıca kullanıcının girdiği kart bilgilerini ekleyin.

**Form Yapısı:**
```html
<form action="https://www.paytr.com/odeme" method="POST">
    <!-- Backend Response 'fields' içindeki her şeyi buraya hidden olarak basın -->
    <input type="hidden" name="merchant_id" value="...">
    <input type="hidden" name="paytr_token" value="...">
    <!-- ... diğerleri ... -->

    <!-- Kullanıcıdan Alınacaklar -->
    <input type="text" name="cc_owner" placeholder="Kart Sahibi">
    <input type="text" name="card_number" placeholder="Kart No">
    <select name="expiry_month">...</select>
    <select name="expiry_year">...</select>
    <input type="text" name="cvv" placeholder="CVV">

    <button type="submit">Öde</button>
</form>
```

### Adım 3: Sonuç (Redirect)
PayTR işlemi tamamladığında tarayıcıyı şu URL'lere yönlendirir (Backend config'inde tanımlıdır):
*   **Başarılı:** `https://api.innovacraftstudio.com/paytr/success` -> Frontend buradan "Siparişiniz Alındı" sayfasına yönlendirmeli.
*   **Başarısız:** `https://api.innovacraftstudio.com/paytr/fail` -> Frontend buradan "Hata" sayfasına yönlendirmeli.

---

## 3. Taksitli Ödeme Mantığı (Önemli)

Eğer kullanıcı **3 Taksit** seçeneğini seçerse:

1.  Backend'e gönderilen `basket` tutarına otomatik olarak **%15 Vade Farkı** eklenir. Frontend'in ayrıca hesaplamasına gerek yoktur.
2.  Backend, PayTR'dan gelen token'ı bu **zamlı fiyat** üzerinden (Nihai Tutar) alır.
3.  **Zorunluluk:** Frontend, `/paytr/direct/init` isteği atarken **`bin_number`** (Kart numarasının ilk 6 hanesi) alanını DOLU göndermelidir.
    *   Backend bu BIN numarasını PayTR'a sorar (Örn: "Bu bi Maximum kart mı?").
    *   Eğer kart taksite uygunsa Token üretir. Uygun değilse hata döner.
4.  Form post edilirken kullanılan kart, `init` aşamasında gönderilen BIN ile **aynı banka markasına** sahip olmalıdır.

---

## 4. Manuel Sipariş Oluşturma (Havale / EFT)

Kredi kartı kullanılmayacaksa, sipariş doğrudan oluşturulur.

*   **Endpoint:** `POST /orders` (veya `/shipping_manual`)
*   **Auth:** Bearer Token (User) gerekir.

**Request Body (JSON):**
Gövde göndermeye gerek yoktur (Body boş olabilir). Backend, kullanıcının **mevcut sepetini (`carts/{user_id}`)** otomatik olarak çeker ve siparişe dönüştürür.

*   Sepet boşsa `400 Bad Request` döner.
*   Başarılıysa `200 OK` döner, sepet temizlenir ve sipariş `preparing` statüsüne geçer.

**Response:**
```json
{
  "id": "OLUSAN_SIPARIS_ID",
  "message": "Siparişiniz alındı...",
  "status": "preparing",
  "totals": { ... }
}
```

---

## 5. Siparişleri Listeleme (User Dashboard)

Kullanıcının siparişlerini listelemek için tek bir endpoint kullanılır.

*   **Endpoint:** `GET /orders`
*   **Query Param:** `view_type` ("active" veya "past")

### A) Aktif Siparişler (`view_type=active`)
Şu statüdeki siparişleri getirir:
*   `preparing` (Hazırlanıyor / Ödeme Onaylandı)
*   `shipped` (Kargoda)

### B) Geçmiş Siparişler (`view_type=past`)
Şu statüdeki siparişleri getirir:
*   `delivered` (Teslim Edildi)
*   `canceled` (İptal)
*   `payment_failed` (Ödeme Başarısız)

**Örnek İstek:** `GET /orders?view_type=active&limit=20`

---

## Frontend Ekibi İçin Özet Kontrol Listesi

- [ ] **Sepet Tutarı:** Kullanıcıya gösterilen tutar ile PayTR'a giden tutarın (özellikle taksitte) backend tarafından yönetildiğini unutmayın.
- [ ] **BIN Numarası:** Taksit seçeneği sunulacaksa, kullanıcı kart numarasını girerken ilk 6 haneyi alıp backend `init` servisine göndermelisiniz.
- [ ] **Ödeme Sonrası:** PayTR'dan dönen `success` sayfasında kullanıcıya "Siparişiniz ID: X ile alındı" demek için Backend'den sipariş listesini tekrar çekip en son siparişi gösterebilirsiniz.
- [ ] **Sepet Temizliği:** Başarılı ödeme sonrası Backend sepeti otomatik temizler. Frontend'in de local state'deki sepeti temizlemesi gerekir (Bunu genellikle sipariş listesi yenilendiğinde veya success sayfasında yapabilirsiniz).
