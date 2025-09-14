# app/core/auth.py
from typing import Optional
from fastapi import Request, HTTPException, status
from firebase_admin import auth as fb_auth
from app.schemas.principal import Principal

def _extract_bearer_token(request: Request) -> Optional[str]:
    """
    Authorization: Bearer <id_token> başlığından token'ı alır.
    Yoksa None döner.
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return None
    parts = auth_header.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None

def _decode_id_token(id_token: str) -> dict:
    """
    Firebase ID token doğrulaması.
    Mock token'ları da geçerli kabul eder (development için).
    Geçersiz/iptal/expired durumda 401 döndürür.
    """
    # Mock token kontrolü (development için)
    if id_token.startswith("mock_jwt_token_"):
        return _decode_mock_token(id_token)
    
    try:
        return fb_auth.verify_id_token(id_token, check_revoked=True)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid Firebase ID token: {exc}"
        )

def _decode_mock_token(mock_token: str) -> dict:
    """
    Mock token'ı decode eder.
    Format: mock_jwt_token_anonymous_1234567890
    """
    if not mock_token.startswith("mock_jwt_token_"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid mock token format"
        )
    
    # Token'dan UID'i çıkar
    uid = mock_token.replace("mock_jwt_token_", "")
    
    # Mock token için decoded data oluştur
    return {
        "uid": uid,
        "user_id": uid,
        "email": None,
        "name": None,
        "firebase": {
            "sign_in_provider": "anonymous" if "anonymous" in uid else "password"
        },
        "admin": False,  # Mock token'lar admin değil
    }

def _token_to_principal(decoded: dict) -> Principal:
    """
    Token'dan Principal üretir.
    - anonymous provider → role='guest'
    - custom claim admin=True → role='admin'
    - diğerleri → role='user'
    """
    uid = decoded.get("uid") or decoded.get("user_id")
    if not uid:
        raise HTTPException(status_code=401, detail="Token missing uid.")

    firebase_info = decoded.get("firebase") or {}
    provider = firebase_info.get("sign_in_provider")
    is_admin = bool(decoded.get("admin") is True)

    if provider == "anonymous":
        role = "guest"
    elif is_admin:
        role = "admin"
    else:
        role = "user"

    return Principal(
        uid=uid,
        role=role,
        email=decoded.get("email"),
        display_name=decoded.get("name"),
    )

# --------- FastAPI Dependencies --------- #

async def get_optional_principal(request: Request) -> Optional[Principal]:
    """
    Token opsiyonel: varsa doğrular ve Principal döner; yoksa None.
    Public GET uçlarında kullanışlıdır.
    """
    token = _extract_bearer_token(request)
    if not token:
        return None
    decoded = _decode_id_token(token)
    return _token_to_principal(decoded)

async def get_principal(request: Request) -> Principal:
    """
    Token zorunlu: doğrular ve Principal döner.
    (guest/user/admin hepsi olabilir)
    """
    token = _extract_bearer_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Missing Authorization header.")
    decoded = _decode_id_token(token)
    return _token_to_principal(decoded)
