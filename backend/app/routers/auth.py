"""
app/routers/auth.py - Authentication routes for user registration (and possibly login).
Uses Firebase Auth for creating accounts and verifying credentials.
"""
from fastapi import APIRouter, Depends, HTTPException, status , Form
from firebase_admin import auth as firebase_auth , _auth_utils
from app.schemas.user import UserCreate, UserProfile
from app.core.security import (get_current_user)
from app.config import db
import re
from pydantic import EmailStr
import httpx
from app.config import settings
from app.schemas.user  import LoginRequest, LoginResponse

router = APIRouter(prefix="/auth", tags=["Auth"])


FIREBASE_SIGNIN_ENDPOINT = (
    "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
    f"?key={settings.firebase_web_api_key}"
)

PHONE_PATTERN = re.compile(r"^\d{3}\s\d{3}\s\d{4}$")   # 555 123 4567

@router.post(
    "/register",
    response_model=UserProfile,
    status_code=status.HTTP_201_CREATED,
    summary="Yeni kullanıcı kaydı",
)
def register(
    name: str = Form(..., min_length=1, description="Ad Soyad"),
    phone: str = Form(..., description="Telefon (555 123 4567)"),
    email: EmailStr = Form(..., description="E-posta"),
    password: str = Form(..., min_length=6, description="Şifre (min 6 karakter)"),
):
    """Form verisiyle kullanıcı oluşturur."""
    # Telefon biçim denetimi
    if not PHONE_PATTERN.fullmatch(phone):
        raise HTTPException(422, "Telefon biçimi '555 123 4567' olmalı")

    # Firebase kaydı
    try:
        rec = firebase_auth.create_user(
            email=email,
            password=password,
            display_name=name,
        )
    except _auth_utils.EmailAlreadyExistsError:
        raise HTTPException(400, "Bu e-posta zaten kayıtlı")
    except Exception as exc:
        raise HTTPException(400, str(exc))

    uid = rec.uid

    profile_doc = {
        "name": name,
        "email": email,
        "phone": phone,
        "role": "customer",
        "addresses": [],
        "created_at": None,
        "is_guest": False,
    }
    db.collection("users").document(uid).set(profile_doc)

    return {**profile_doc, "id": uid}

# Optionally, if we wanted to implement login via backend (not typical since client handles it, but for completeness):
# We could verify email/password by calling Firebase's REST API or custom token creation, but it's simpler to let front-end handle login.
# Therefore, we do not implement a /login endpoint here. The user obtains JWT from Firebase client SDK.

@router.post("/reset-password")
def request_password_reset(email: str):
    """
    Initiates a password reset email via Firebase.
    Sends a reset link to the given email if it exists.
    """
    try:
        link = firebase_auth.generate_password_reset_link(email)
        # In a real system, we'd send this link via an email service to the user.
        # For now, just log or pretend it's sent.
        print(f"Password reset link generated for {email}: {link}")
    except Exception as e:
        # If email not found or other error, we return success message to avoid user enumeration
        print(f"Reset password attempted for {email}: {e}")
    # Always return success message (do not reveal if email exists or not)
    return {"detail": "If an account with that email exists, a password reset link has been sent."}



@router.post(
    "/login",
    response_model=LoginResponse,
    summary="E-posta + şifre ile giriş",
)
async def login(
    email:    EmailStr = Form(..., description="E-posta"),
    password: str      = Form(..., min_length=6, description="Şifre (≥6 kr.)"),
):
    """Form verisiyle Firebase’e proxy olur, id_token + refresh_token döndürür."""
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
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=message)

    return LoginResponse(
        id_token      = data["idToken"],
        refresh_token = data["refreshToken"],
        expires_in    = int(data["expiresIn"]),
        user_id       = data["localId"],
    )


# --------------------------------------------------------------------------- #
# LOGOUT – refresh token’ları iptal eder, ID token’ı geçersiz kılar
# --------------------------------------------------------------------------- #
@router.post("/logout", summary="Sunucu tarafında oturumu kapat")
def logout(current_user = Depends(get_current_user)):
    """Kullanıcının bütün refresh token’larını iptal eder."""
    try:
        firebase_auth.revoke_refresh_tokens(current_user["id"])
    except _auth_utils.UserNotFoundError:
        # Uygun olmayan durum – sessizce geç
        pass
    return {"detail": "Logged out"}