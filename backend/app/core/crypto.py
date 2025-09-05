import hmac, hashlib, secrets
from app.config import settings

def delete_secret() -> str:
    return getattr(settings, "delete_account_secret", None) or settings.secret_key

def gen_numeric_code(n: int) -> str:
    return str(secrets.randbelow(10 ** n)).zfill(n)

def hmac_hash(uid: str, code: str) -> str:
    key = delete_secret().encode("utf-8")
    msg = f"{uid}:{code}".encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()
