"""
# `app/core/security.py` — Güvenlik & Kimlik Doğrulama Dokümantasyonu

Bu modül, **Firebase ID Token** doğrulaması ile kimlik denetimi yapan FastAPI bağımlılıklarını ve **rol bazlı yetkilendirme** yardımcılarını içerir. Endpoint’lerde `Depends(...)` ile kullanılarak kullanıcı veya admin erişimi sağlanabilir.

---

## Genel İşleyiş

- **Kimlik Doğrulama:** `Authorization: Bearer <Firebase ID token>` başlığı ile gelen token, **Firebase Admin SDK** ile doğrulanır.
- **Kullanıcı Getirme / Oluşturma:** Token doğrulandıktan sonra Firestore’daki `users/{uid}` dokümanı okunur. Yoksa token’dan gelen bilgilerle varsayılan bir profil oluşturulur.
- **Rol Kontrolü:** `get_current_admin` fonksiyonu, kullanıcının `role` alanının `"admin"` olup olmadığını kontrol eder.

> **Not:** `firebase_admin` başlatımı (`firebase_admin.initialize_app(...)`) uygulama genelinde yapılmış olmalıdır. Ayrıca `app.config.db` Firestore istemcisini (`db`) sağlamalıdır.

---

## Güvenlik Şeması

### `oauth2_scheme = HTTPBearer(auto_error=False)`
- **Amaç:** `Authorization` başlığındaki Bearer token’ı almak için kullanılır.
- `auto_error=False` ile token yoksa hata fırlatma kontrolü bizde olur.

**Beklenen Header:**

"""
"""
---

## Fonksiyonlar

### 1) `get_current_user(token: HTTPAuthorizationCredentials = Depends(oauth2_scheme)) -> dict`
**Amaç:** Geçerli kullanıcıyı doğrulamak ve döndürmek.

**Adımlar:**
1. **Token kontrolü:** Token yoksa `401 Unauthorized` hatası döner.
2. **Token doğrulama:**  
   `firebase_auth.verify_id_token(...)` ile Firebase ID Token doğrulanır. Geçersizse `401 Unauthorized` döner.
3. **Kullanıcı verisi çekme:**  
   Firestore’daki `users/{uid}` dokümanı alınır.
4. **Yoksa oluşturma:**  
   Kullanıcı dokümanı yoksa token’dan alınan `name`, `email`, `phone_number` bilgileriyle varsayılan bir profil (`role="customer"`, `addresses=[]`, `is_guest=False`) oluşturulur.
5. **Sonuç:**  
   Kullanıcı verisi `id` alanıyla birlikte döndürülür.

**Dönen Değer Örneği:**
```json
{
  "id": "firebase_uid",
  "name": "Ad Soyad",
  "email": "email@example.com",
  "phone": "+90...",
  "role": "customer",
  "addresses": [],
  "is_guest": false
}
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from firebase_admin import auth as firebase_auth
from app import config
from app.config import settings, db
from firebase_admin import firestore

# HTTPBearer is a FastAPI provided security scheme for "Authorization: Bearer <token>" header
oauth2_scheme = HTTPBearer(auto_error=False)

def get_current_user(token: HTTPAuthorizationCredentials = Depends(oauth2_scheme)):
    """
    Dependency to get the currently authenticated user.
    It verifies the Firebase ID token and returns user data (from Firestore) if valid.
    If no token or an invalid token is provided, raises HTTP 401.
    """
    if token is None:
        # No token provided
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided",
            headers={"WWW-Authenticate": "Bearer"}
        )
    try:
        # Verify the token with Firebase Admin SDK. This throws if invalid.
        decoded_token = firebase_auth.verify_id_token(token.credentials)
        uid = decoded_token.get('uid')
    except Exception as exc:
        # Verification failed
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"}
        )
    # Fetch user profile from Firestore
    user_ref = db.collection('users').document(uid)
    doc = user_ref.get()
    if not doc.exists:
        # If user profile doesn't exist yet, create one using info from token (for social logins)
        # We can extract name/email from decoded_token if available
        user_data = {
            "name": decoded_token.get('name', ""),
            "email": decoded_token.get('email', ""),
            "phone": decoded_token.get('phone_number', ""),
            "role": "customer",
            "addresses": [],
            "created_at": firestore.SERVER_TIMESTAMP,
            "is_guest": False
        }
        user_ref.set(user_data)
        user = user_data
        user['id'] = uid
    else:
        user = doc.to_dict()
        user['id'] = uid
    # Attach the UID and role for further use
    return user

def get_current_admin(current_user: dict = Depends(get_current_user)):
    """
    Dependency to allow access only to admin users.
    Uses get_current_user to authenticate, then checks the role.
    """
    if current_user.get('role') != 'admin':
        # The user is authenticated but not an admin
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return current_user

# Additional security helpers (if needed):
# e.g., password hashing for manual auth or generating secure tokens for invites, etc.
# Not used here since Firebase manages authentication.
