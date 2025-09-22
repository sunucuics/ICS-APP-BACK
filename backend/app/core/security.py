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
from backend.app.config import settings, db
from firebase_admin import firestore
from backend.app.schemas.principal import Principal
from backend.app.core.auth import get_principal
from typing import Optional, Dict
# HTTPBearer is a FastAPI provided security scheme for "Authorization: Bearer <token>" header
oauth2_scheme = HTTPBearer(auto_error=False)

def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(oauth2_scheme)
) -> Dict:
    """
    Firebase ID token'ı doğrular (revocation kontrolü ile).
    Kullanıcı profili Firestore'da yoksa oluşturur ve kullanıcıyı döner.
    """
    if not credentials or not credentials.scheme or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided",
            headers={"WWW-Authenticate": "Bearer"},
        )

    id_token = credentials.credentials
    try:
        # ÖNEMLİ: check_revoked=True -> logout sonrası token'lar reddedilir
        decoded = firebase_auth.verify_id_token(id_token, check_revoked=True)
    except firebase_auth.ExpiredIdTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except firebase_auth.RevokedIdTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    uid = decoded.get("uid")
    if not uid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
            headers={"WWW-Authenticate": "Bearer"},
        )

    provider = (decoded.get("firebase") or {}).get("sign_in_provider")
    is_guest = (provider == "anonymous")

    # Kullanıcı profilini getir/oluştur
    user_ref = db.collection("users").document(uid)
    doc = user_ref.get()

    if not doc.exists:
        user_data = {
            "name": decoded.get("name", "") or "",
            "email": decoded.get("email", "") or "",
            "phone": decoded.get("phone_number", "") or "",
            "email_verified": bool(decoded.get("email_verified")),
            "role": "customer",
            "addresses": [],
            "created_at": firestore.SERVER_TIMESTAMP,
            "is_guest": is_guest,
        }
        user_ref.set(user_data)
        user = {**user_data, "id": uid}
    else:
        user = doc.to_dict() or {}
        user["id"] = uid
        # is_guest alanı yoksa ekle (opsiyonel küçük bakım)
        if "is_guest" not in user:
            user_ref.set({"is_guest": is_guest}, merge=True)
            user["is_guest"] = is_guest

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


def require_non_guest(principal: Principal = Depends(get_principal)) -> Principal:
    """
    Misafir kullanıcıları (role='guest') 403 ile engeller.
    Sipariş/servis gibi aksiyon endpoint'lerinde kullan.
    """
    if principal.role == "guest":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Guest users are not allowed for this action."
        )
    return principal

def require_admin(principal: Principal = Depends(get_principal)) -> Principal:
    """
    Sadece admin kullanıcıları kabul eder.
    """
    if principal.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privilege required."
        )
    return principal