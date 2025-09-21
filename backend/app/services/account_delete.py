from google.api_core.exceptions import NotFound
from firebase_admin import auth as firebase_auth, _auth_utils
from backend.app.core.crypto import gen_numeric_code, hmac_hash
from backend.app.core.constants import (
    DELETE_CODE_TTL_SECONDS,
    DELETE_MAX_ATTEMPTS,
    DELETE_CODE_LENGTH,
)
from backend.app.repositories import delete_requests as repo
from backend.app.core.email_utils import send_email


# --- Silme istek akışı ---

async def initiate(uid: str, email: str, display_name: str | None = "") -> None:
    """
    Kullanıcı için tek kullanımlık doğrulama kodu üretir, hash'ler, Firestore'a yazar
    ve e-posta ile gönderir.
    """
    code = gen_numeric_code(DELETE_CODE_LENGTH)
    code_hash = hmac_hash(uid, code)
    repo.create_or_replace(uid, code_hash, DELETE_CODE_TTL_SECONDS)

    subject = "Hesap Silme Doğrulama Kodun"
    html = f"""<div style="font-family:Arial,sans-serif">
      <h2>Hesap silme onayı</h2>
      <p>Merhaba {display_name or ""},</p>
      <p>Doğrulama kodun:</p>
      <p style="font-size:24px;font-weight:bold;letter-spacing:3px">{code}</p>
      <p>Bu kod 30 dakika geçerlidir. Paylaşmayın.</p>
    </div>"""
    await send_email(email, subject, html)


def _cleanup_user_data(uid: str) -> None:
    """
    (Opsiyonel) Kullanıcıya bağlı diğer koleksiyonları temizlemek için örnek.
    İhtiyacın yoksa silebilirsin.
    """
    from backend.app.config import db

    to_clean = [
        ("addresses", "user_id"),
        ("orders", "user_id"),
        ("notification_tokens", "uid"),
        # ("carts", "user_id"), ...
    ]
    for col, field in to_clean:
        q = db.collection(col).where(field, "==", uid).stream()
        batch = db.batch()
        n = 0
        for doc in q:
            batch.delete(doc.reference)
            n += 1
            if n % 400 == 0:
                batch.commit()
                batch = db.batch()
        batch.commit()


def _revoke_and_delete_user(uid: str) -> None:
    """
    Refresh token'ları iptal eder, Firestore profilini ve Auth hesabını siler.
    """
    try:
        firebase_auth.revoke_refresh_tokens(uid)
    except _auth_utils.UserNotFoundError:
        pass

    # Firestore profilini sil
    from backend.app.config import db
    try:
        db.collection("users").document(uid).delete()
    except NotFound:
        pass

    # İsteğe bağlı: bağlı verileri temizle
    _cleanup_user_data(uid)

    # Firebase Auth hesabını sil
    try:
        firebase_auth.delete_user(uid)
    except _auth_utils.UserNotFoundError:
        pass


def verify_and_delete(uid: str, code: str) -> None:
    """
    Kullanıcının girdiği kodu doğrular. Doğruysa hesabı kalıcı olarak siler.
    """
    rec = repo.get(uid)
    if not rec or rec.get("consumed"):
        raise ValueError("NO_ACTIVE_REQUEST")

    if repo.now_ts() > int(rec.get("expires_at_unix", 0)):
        repo.consume(uid)
        raise ValueError("EXPIRED")

    attempts = int(rec.get("attempts", 0))
    if attempts >= DELETE_MAX_ATTEMPTS:
        repo.consume(uid)
        raise ValueError("TOO_MANY_ATTEMPTS")

    if hmac_hash(uid, code) != rec.get("code_hash"):
        repo.increment_attempt(uid)
        raise ValueError("INVALID_CODE")

    _revoke_and_delete_user(uid)
    repo.consume(uid)
