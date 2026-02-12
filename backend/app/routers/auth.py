"""
aa
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
from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status , Form , Query , Header , Request
import os
import logging
from firebase_admin import auth as firebase_auth , _auth_utils
from starlette.concurrency import run_in_threadpool
from backend.app.schemas.user import ProfileUpdateRequest, UserCreate, UserProfile , LoginRequest, LoginResponse , RegisterResponse, RefreshTokenRequest, RefreshTokenResponse
from backend.app.core.security import (get_current_user, get_current_user_strict, invalidate_user_cache)
from backend.app.core.http_client import get_http_client, retry_with_backoff
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
    phone: Optional[str] = Form(None, description="Telefon (opsiyonel, 555 123 4567)"),
    email: EmailStr = Form(..., description="E-posta"),
    password: str = Form(..., min_length=6, description="Şifre (min 6 karakter)"),
    fcm_token: Optional[str] = Form(None, description="FCM Token (opsiyonel)"),
    authorization: Optional[str] = Header(
        None,
        alias="Authorization",
        description="Bearer <Firebase ID Token> (opsiyonel)",
    ),
):
    # 1) UID elde et: header varsa token doğrula; yoksa Admin SDK ile user yarat
    uid: Optional[str] = None
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

    # 2) Telefon formatı (sadece girildiyse kontrol et)
    # boş string gelirse de None gibi davranması için strip ile normalize edelim
    if phone is not None:
        phone = phone.strip() or None

    if phone and not PHONE_PATTERN.fullmatch(phone):
        raise HTTPException(422, "Telefon biçimi '555 123 4567' olmalı")

    # 3) Email eşleşmesi
    user_record = firebase_auth.get_user(uid)
    if user_record.email and user_record.email.lower() != email.lower():
        raise HTTPException(400, "Firebase UID ile email eşleşmiyor")

    # 4) Firestore'da var mı?
    user_ref = db.collection("users").document(uid)
    doc_snap = user_ref.get()

    if doc_snap.exists:
        # DB'de kayıt var. Ancak "name" alanı boşsa (hatalı/eksik kayıt),
        # tekrar kayıt olmaya (üzerine yazmaya) izin ver.
        # Sadece "name" doluysa "Zaten kayıtlı" hatası at.
        existing_data = doc_snap.to_dict() or {}
        if existing_data.get("name"):
            raise HTTPException(400, "Bu kullanıcı zaten kayıtlı")

    # 5) Profil yaz
    # Telefon gelmediyse "0" olarak kaydediyoruz
    stored_phone = phone or None

    profile_doc: dict = {
        "name": name,
        "email": email,
        "phone": stored_phone,
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
            @retry_with_backoff(max_retries=1, backoff_factor=0.3)
            async def _firebase_signin():
                client = get_http_client()
                return await client.post(
                    f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={settings.firebase_web_api_key}",
                    json={
                        "email": email,
                        "password": password,
                        "returnSecureToken": True,
                    },
                )
            
            r = await _firebase_signin()
            if r.status_code == 200:
                data = r.json()
                id_tok = data["idToken"]
                refresh_tok = data["refreshToken"]
                exp = int(data["expiresIn"])
            else:
                logging.warning("Login proxy failed: %s %s", r.status_code, r.text)
        except httpx.TimeoutException as e:
            logging.warning(f"Login proxy timeout: {e}")
        except httpx.ConnectError as e:
            logging.warning(f"Login proxy connection error: {e}")
        except Exception as e:
            logging.warning(f"Login proxy error: {e}")

    user_out = UserProfile(
        id=uid,
        name=name,
        email=email,
        phone=stored_phone,  # her durumda ya gerçek numara ya da "0"
        role="customer",
        addresses=[],
        created_at=None,
        is_guest=False,
    )
    return RegisterResponse(
        user_id=uid,
        user=user_out,
        id_token=id_tok,
        refresh_token=refresh_tok,
        expires_in=exp,
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
        @retry_with_backoff(max_retries=1, backoff_factor=0.3)
        async def _send_reset_email():
            client = get_http_client()
            return await client.post(url, json=payload, headers=headers)
        
        r = await _send_reset_email()
        
        # Güvenli/generic response (EMAIL_NOT_FOUND vs. sızdırma yapma)
        if r.status_code == 200:
            return {"message": "Eğer bu e-posta kayıtlıysa, şifre sıfırlama e-postası gönderildi."}
        else:
            # Sık görülen hata: EMAIL_NOT_FOUND (400). Yine generic dönüyoruz.
            logging.warning("sendOobCode response: %s %s", r.status_code, r.text)
            return {"message": "Eğer bu e-posta kayıtlıysa, şifre sıfırlama e-postası gönderildi."}
    except httpx.TimeoutException as e:
        logging.error(f"Password reset timeout: {e}")
        return {"message": "Eğer bu e-posta kayıtlıysa, şifre sıfırlama e-postası gönderildi."}
    except httpx.ConnectError as e:
        logging.error(f"Password reset connection error: {e}")
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
    payload = {
        "email": email,
        "password": password,
        "returnSecureToken": True,
    }

    try:
        @retry_with_backoff(max_retries=1, backoff_factor=0.3)
        async def _firebase_login():
            client = get_http_client()
            return await client.post(FIREBASE_SIGNIN_ENDPOINT, json=payload)
        
        resp = await _firebase_login()
        data = resp.json()
        
        if resp.status_code != 200:
            message = data.get("error", {}).get("message", "Invalid credentials")
            logging.error(f"Firebase login failed: {message}")
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=message)
            
    except httpx.TimeoutException as e:
        logging.error(f"Firebase login timeout: {e}")
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Authentication service timeout. Please try again."
        )
    except httpx.ConnectError as e:
        logging.error(f"Firebase login connection error: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Cannot connect to authentication service. Please try again."
        )
    except HTTPException:
        raise
    except Exception as e:
        logging.exception(f"Unexpected login error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred during login"
        )

    # FCM token varsa kullanıcı profilini güncelle
    if fcm_token:
        try:
            user_id = data["localId"]
            
            # Non-blocking update using threadpool
            def update_token_sync():
                db.collection("users").document(user_id).update({
                    "fcm_token": fcm_token
                })
                
            await run_in_threadpool(update_token_sync)
        except Exception as e:
            logging.warning(f"Failed to update FCM token: {e}")

    return LoginResponse(
        id_token      = data["idToken"],
        refresh_token = data["refreshToken"],
        expires_in    = int(data["expiresIn"]),
        user_id       = data["localId"],
    )


# --------------------------------------------------------------------------- #
# REFRESH TOKEN – refresh token ile yeni ID token al
# --------------------------------------------------------------------------- #
@router.post(
    "/refresh",
    response_model=RefreshTokenResponse,
    summary="Refresh token ile yeni access token al",
)
async def refresh_token(request: RefreshTokenRequest):
    """
    Firebase refresh token kullanarak yeni ID token alır.
    Refresh token geçersizse 401 döner.
    """
    if not settings.firebase_web_api_key:
        raise HTTPException(
            status_code=500,
            detail="Server misconfigured: missing FIREBASE_WEB_API_KEY"
        )

    url = f"https://securetoken.googleapis.com/v1/token?key={settings.firebase_web_api_key}"
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": request.refresh_token,
    }
    headers = {
        "Content-Type": "application/json",
    }

    try:
        # Firebase token refresh with retry mechanism
        @retry_with_backoff(max_retries=1, backoff_factor=0.3)
        async def _firebase_refresh():
            client = get_http_client()
            return await client.post(url, json=payload, headers=headers)
        
        logging.info(f"Attempting token refresh for request token: {request.refresh_token[:20]}...")
        resp = await _firebase_refresh()
        data = resp.json()
        
        if resp.status_code != 200:
            error_message = data.get("error", {}).get("message", "Invalid refresh token")
            logging.warning(f"Firebase refresh token failed: {error_message}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=error_message
            )

        logging.info("Token refresh successful")
        # Firebase'den dönen expires_in değerini olduğu gibi kullan (genelde 3600 = 1 saat)
        # Client tarafı bu değere göre proaktif refresh zamanlayacak
        return RefreshTokenResponse(
            id_token=data["id_token"],
            refresh_token=data.get("refresh_token", request.refresh_token),  # Yeni refresh token varsa onu kullan
            expires_in=int(data.get("expires_in", 3600)),
        )
        
    except httpx.TimeoutException as e:
        logging.error(f"Token refresh timeout after retries: {e}")
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Authentication service timeout. Please try again."
        )
    except httpx.ConnectError as e:
        logging.error(f"Token refresh connection error after retries: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Cannot connect to authentication service. Please try again later."
        )
    except httpx.NetworkError as e:
        logging.error(f"Token refresh network error: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Network error occurred. Please check your connection."
        )
    except httpx.HTTPStatusError as e:
        logging.error(f"Token refresh HTTP status error: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Authentication service returned an error."
        )
    except HTTPException:
        # Re-raise HTTPException as-is (401 from above)
        raise
    except Exception as e:
        logging.exception(f"Unexpected token refresh error: {type(e).__name__}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred during token refresh. Please try logging in again."
        )


# --------------------------------------------------------------------------- #
# LOGOUT – refresh token'ları iptal eder, ID token'ı geçersiz kılar
# --------------------------------------------------------------------------- #
@router.post("/logout", summary="Sunucu tarafında oturumu kapat (refresh revoke)")
def logout(current_user: dict = Depends(get_current_user_strict)):
    """
    Tüm cihazlardaki refresh token'ları iptal eder.
    İstemci ayrıca firebase SDK'da signOut() çağırmalıdır.
    """
    uid = current_user["id"]
    # Cache'den kullanıcı profilini temizle (yeni oturum güncel veri alsın)
    invalidate_user_cache(uid)
    try:
        firebase_auth.revoke_refresh_tokens(uid)
    except Exception:
        # Kullanıcı silinmiş vs. ise sessizce geçiyoruz.
        pass
    return {"detail": "Logged out"}

@router.put(
    "/me",
    response_model=UserProfile,
    summary="Kullanıcı profili güncelle (me)",
)
async def update_my_profile(
    payload: ProfileUpdateRequest,
    current_user: dict = Depends(get_current_user),
):
    uid = current_user["id"]

    user_ref = db.collection("users").document(uid)
    snap = user_ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="User profile not found")

    updates: Dict[str, Any] = {}

    # NAME
    if payload.name is not None:
        updates["name"] = payload.name.strip()

    # PHONE
    if payload.phone is not None:
        phone = payload.phone.strip()
        phone = phone or None  # "" -> None
        if phone and not PHONE_PATTERN.fullmatch(phone):
            raise HTTPException(status_code=422, detail="Telefon biçimi '555 123 4567' olmalı")
        updates["phone"] = phone

    # EMAIL (Firebase Auth + Firestore)
    if payload.email is not None:
        new_email = payload.email.strip().lower()
        try:
            firebase_auth.update_user(uid, email=new_email)
        except firebase_auth.EmailAlreadyExistsError:
            raise HTTPException(status_code=409, detail="Bu e-posta zaten kullanılıyor")
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Email update failed: {e}")

        updates["email"] = new_email

    if not updates:
        raise HTTPException(status_code=400, detail="No fields provided to update")

    updates["updated_at"] = gcf.SERVER_TIMESTAMP
    user_ref.update(updates)
    # Profil değişti → cache'den eski veriyi temizle
    invalidate_user_cache(uid)

    fresh = user_ref.get().to_dict() or {}
    return UserProfile(
        id=uid,
        name=fresh.get("name"),
        email=fresh.get("email"),
        phone=fresh.get("phone"),
        role=fresh.get("role", "customer"),
        addresses=fresh.get("addresses", []) or [],  # UserProfile şeman bunu istiyor, boş döner
    )
