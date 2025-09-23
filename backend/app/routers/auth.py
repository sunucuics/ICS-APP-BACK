"""
# app/routers/auth.py — Kimlik Doğrulama Dokümantasyonu

## Genel Bilgi
Bu dosya, kullanıcıların kayıt, giriş, şifre sıfırlama ve çıkış işlemlerini yönetir. Firebase Authentication kullanılarak kimlik doğrulama yapılır ve Firestore’da kullanıcı profili saklanır.

---

## Endpoint’ler

### POST /auth/register
Amaç: Yeni kullanıcı kaydı oluşturmak.

Parametreler (Form-Data):
- name: Ad Soyad (min. 1 karakter)
- phone: Telefon numarası (555 123 4567 formatında)
- email: E-posta adresi
- password: Şifre (min. 6 karakter)

İşleyiş:
1. Telefon numarası format kontrolü yapılır.
2. Firebase’de kullanıcı oluşturulur.
3. Firestore’da users/{uid} dokümanı oluşturulur.
4. Kullanıcı bilgileri id ile birlikte döndürülür.

---

### POST /auth/reset-password
Amaç: Şifre sıfırlama bağlantısı göndermek.

Parametreler:
- email: Bağlantının gönderileceği e-posta.

İşleyiş:
1. Firebase üzerinden şifre sıfırlama bağlantısı üretilir.
2. Her durumda başarılı yanıt döndürülür (kullanıcı var/yok bilgisi verilmez).

---

### POST /auth/login
Amaç: E-posta ve şifre ile giriş yapmak.

Parametreler (Form-Data):
- email: E-posta adresi
- password: Şifre (min. 6 karakter)

İşleyiş:
1. Firebase REST API’sine giriş isteği gönderilir.
2. Başarılıysa id_token, refresh_token, expires_in, user_id döndürülür.
3. Başarısızsa 401 Unauthorized döner.

---

### POST /auth/logout
Amaç: Oturumu kapatmak.

İşleyiş:
1. get_current_user ile kimlik doğrulama yapılır.
2. Firebase’de kullanıcının tüm refresh token’ları iptal edilir.
3. "Logged out" mesajı döndürülür.

"""
from fastapi import APIRouter, Depends, HTTPException, status , Form , Query , Header , Request
import os
import logging
from firebase_admin import auth as firebase_auth , _auth_utils
from backend.app.schemas.user import UserCreate, UserProfile , LoginRequest, LoginResponse , RegisterResponse
from backend.app.core.security import (get_current_user)
from backend.app.config import db
import re
from pydantic import EmailStr
import httpx
from backend.app.config import settings
from google.cloud import firestore as gcf
from dotenv import load_dotenv
load_dotenv()

router = APIRouter(prefix="/auth", tags=["Auth"])


FIREBASE_SIGNIN_ENDPOINT = (
    "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
    f"?key={settings.firebase_web_api_key}"
)

PHONE_PATTERN = re.compile(r"^\d{3}\s\d{3}\s\d{4}$")   # 555 123 4567

@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Kayıt: Token'lı (client-first) veya tokensiz (server-first) profil oluştur",
    description=(
        "Authorization: Bearer <ID_TOKEN> varsa onu doğrular. "
        "Yoksa Admin SDK ile Firebase kullanıcısı oluşturur ve Firestore profilini yazar."
    ),
)
async def register(
    request: Request,
    name: str = Form(..., min_length=1, description="Ad Soyad"),
    phone: str = Form(..., description="Telefon (555 123 4567)"),
    email: EmailStr = Form(..., description="E-posta"),
    password: str = Form(..., min_length=6, description="Şifre (min 6 karakter)"),
    fcm_token: str | None = Form(None, description="FCM Token (opsiyonel)"),
    authorization: str | None = Header(None, alias="Authorization",
                                       description="Bearer <Firebase ID Token> (opsiyonel)"),
):
    # 1) UID elde et: header varsa token doğrula; yoksa Admin SDK ile user yarat
    uid: str | None = None
    if authorization and authorization.startswith("Bearer "):
        id_token = authorization[7:]
        try:
            decoded = firebase_auth.verify_id_token(id_token)
            uid = decoded["uid"]
        except Exception as exc:
            raise HTTPException(401, f"Invalid Firebase token: {exc}")
    else:
        # Token yok → server-first kayıt
        try:
            user = firebase_auth.create_user(
                email=email, password=password, display_name=name
            )
            uid = user.uid
        except firebase_auth.EmailAlreadyExistsError:
            # Hesap zaten varsa uid'yi çek
            user = firebase_auth.get_user_by_email(email)
            uid = user.uid
        except Exception as exc:
            raise HTTPException(400, f"Firebase kullanıcı oluşturma hatası: {exc}")

    # 2) Telefon formatı
    if not PHONE_PATTERN.fullmatch(phone):
        raise HTTPException(422, "Telefon biçimi '555 123 4567' olmalı")

    # 3) Email eşleşmesi
    user_record = firebase_auth.get_user(uid)
    if user_record.email and user_record.email.lower() != email.lower():
        raise HTTPException(400, "Firebase UID ile email eşleşmiyor")

    # 4) Firestore'da var mı?
    user_ref = db.collection("users").document(uid)
    if user_ref.get().exists:
        raise HTTPException(400, "Bu kullanıcı zaten kayıtlı")

    # 5) Profil yaz
    profile_doc = {
        "name": name,
        "email": email,
        "phone": phone,
        "role": "customer",
        "addresses": [],
        "created_at": gcf.SERVER_TIMESTAMP,
        "is_guest": False,
    }
    if fcm_token:
        profile_doc["fcm_token"] = fcm_token
    user_ref.set(profile_doc)

    # 6) Cevap (tokenlar)
    # - Tokenlı çağrıda zaten client'ta token var → boş dön.
    # - Token yoksa ve API key varsa, arka planda sign-in yapıp token döndürmeyi dener (opsiyonel).
    id_tok, refresh_tok, exp = "", "", 3600
    if not authorization and settings.firebase_web_api_key:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={settings.firebase_web_api_key}",
                    json={"email": email, "password": password, "returnSecureToken": True},
                )
            if r.status_code == 200:
                data = r.json()
                id_tok = data["idToken"]
                refresh_tok = data["refreshToken"]
                exp = int(data["expiresIn"])
            else:
                logging.warning("Login proxy failed: %s %s", r.status_code, r.text)
        except Exception as e:
            logging.warning(f"Login proxy error: {e}")

    user_out = UserProfile(
        id=uid, name=name, email=email, phone=phone,
        role="customer", addresses=[], created_at=None, is_guest=False
    )
    return RegisterResponse(
        user_id=uid, user=user_out,
        id_token=id_tok, refresh_token=refresh_tok, expires_in=exp
    )

# Optionally, if we wanted to implement login via backend (not typical since client handles it, but for completeness):
# We could verify email/password by calling Firebase's REST API or custom token creation, but it's simpler to let front-end handle login.
# Therefore, we do not implement a /login endpoint here. The user obtains JWT from Firebase client SDK.

@router.post("/reset-password", summary="Request Password Reset")
async def request_password_reset(
    email: str = Query(..., min_length=5, max_length=254, description="User email")
):
    """
    Triggers Firebase to SEND the password reset email.
    Always returns a generic message (no user enumeration).
    """
    if not settings.firebase_web_api_key:
        raise HTTPException(status_code=500, detail="Server misconfigured: missing FIREBASE_WEB_API_KEY")

    url = f"https://identitytoolkit.googleapis.com/v1/accounts:sendOobCode?key={settings.firebase_web_api_key}"
    payload = {
        "requestType": "PASSWORD_RESET",
        "email": email,
        # Opsiyonel yönlendirme:
        # "continueUrl": "https://yourdomain.page.link/reset"
    }
    headers = {
        "Content-Type": "application/json",
        "X-Firebase-Locale": "tr",  # e-posta dili
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, json=payload, headers=headers)
        # Güvenli/generic response (EMAIL_NOT_FOUND vs. sızdırma yapma)
        if r.status_code == 200:
            return {"message": "Eğer bu e-posta kayıtlıysa, şifre sıfırlama e-postası gönderildi."}
        else:
            # Sık görülen hata: EMAIL_NOT_FOUND (400). Yine generic dönüyoruz.
            logging.warning("sendOobCode response: %s %s", r.status_code, r.text)
            return {"message": "Eğer bu e-posta kayıtlıysa, şifre sıfırlama e-postası gönderildi."}
    except httpx.HTTPError as e:
        logging.exception("sendOobCode failed")
        raise HTTPException(status_code=502, detail=f"Password reset service error: {e}")



@router.post(
    "/login",
    response_model=LoginResponse,
    summary="E-posta + şifre ile giriş",
)
async def login(
    email:    EmailStr = Form(..., description="E-posta"),
    password: str      = Form(..., min_length=6, description="Şifre (≥6 kr.)"),
    fcm_token: str = Form(None, description="FCM Token (opsiyonel)"),
):
    """Form verisiyle Firebase'e proxy olur, id_token + refresh_token döndürür."""
    # Debug: API key'i logla
    logging.error(f"🔥 DEBUG: FIREBASE_WEB_API_KEY = {settings.firebase_web_api_key}")
    logging.error(f"🔥 DEBUG: FIREBASE_SIGNIN_ENDPOINT = {FIREBASE_SIGNIN_ENDPOINT}")
    
    payload = {
        "email": email,
        "password": password,
        "returnSecureToken": True,
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(FIREBASE_SIGNIN_ENDPOINT, json=payload, timeout=10)

    data = resp.json()
    if resp.status_code != 200:
        message = data.get("error", {}).get("message", "Invalid credentials")
        logging.error(f"Firebase login failed: {message}, API Key: {settings.firebase_web_api_key[:10]}...")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=message)

    # FCM token varsa kullanıcı profilini güncelle
    if fcm_token:
        try:
            user_id = data["localId"]
            db.collection("users").document(user_id).update({
                "fcm_token": fcm_token
            })
        except Exception as e:
            logging.warning(f"Failed to update FCM token: {e}")

    return LoginResponse(
        id_token      = data["idToken"],
        refresh_token = data["refreshToken"],
        expires_in    = int(data["expiresIn"]),
        user_id       = data["localId"],
    )


# --------------------------------------------------------------------------- #
# LOGOUT – refresh token’ları iptal eder, ID token’ı geçersiz kılar
# --------------------------------------------------------------------------- #
@router.post("/logout", summary="Sunucu tarafında oturumu kapat (refresh revoke)")
def logout(current_user: dict = Depends(get_current_user)):
    """
    Tüm cihazlardaki refresh token'ları iptal eder.
    İstemci ayrıca firebase SDK'da signOut() çağırmalıdır.
    """
    uid = current_user["id"]
    try:
        firebase_auth.revoke_refresh_tokens(uid)
    except Exception:
        # Kullanıcı silinmiş vs. ise sessizce geçiyoruz.
        pass
    return {"detail": "Logged out"}