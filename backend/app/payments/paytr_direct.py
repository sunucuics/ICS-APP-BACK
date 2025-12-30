# -*- coding: utf-8 -*-
from __future__ import annotations

import os, json, hmac, base64, hashlib, logging, re
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Dict, Optional, Literal , Any
from firebase_admin import firestore
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request, Form, Query , Body
from starlette.responses import PlainTextResponse, HTMLResponse
from pydantic import BaseModel, Field, EmailStr, validator
from backend.app.config import db

# =========================
# ENV
# =========================
PAYTR_MERCHANT_ID = os.getenv("PAYTR_MERCHANT_ID", "")
PAYTR_MERCHANT_KEY = os.getenv("PAYTR_MERCHANT_KEY", "")
PAYTR_MERCHANT_SALT = os.getenv("PAYTR_MERCHANT_SALT", "")
PAYTR_OK_URL = os.getenv("PAYTR_OK_URL", "https://example.com/payment/success")
PAYTR_FAIL_URL = os.getenv("PAYTR_FAIL_URL", "https://example.com/payment/fail")
PAYTR_TEST_MODE = "1" if os.getenv("PAYTR_TEST_MODE", "0").lower() in ("1", "true", "yes") else "0"
PAYTR_SIGN_MODE = os.getenv("PAYTR_SIGN_MODE", "minimal").lower()  # minimal | extended

if not (PAYTR_MERCHANT_ID and PAYTR_MERCHANT_KEY and PAYTR_MERCHANT_SALT):
    raise RuntimeError("PAYTR env missing")

_PREFIX = (os.getenv("FIREBASE_COLLECTION_PREFIX") or "").strip()
def _prefixed(name: str) -> str:
    return f"{_PREFIX}{name}" if _PREFIX else name
_CARTS = _prefixed("carts")

def _clear_cart(user_id: str):
    try:
        cref = db.collection(_CARTS).document(user_id)
        cref.set({"items": []}, merge=True)
        for dsnap in cref.collection("items").stream():
            dsnap.reference.delete()
    except Exception:
        pass

router = APIRouter(prefix="/paytr", tags=["paytr"])

log = logging.getLogger("paytr")
if not log.handlers:
    logging.basicConfig(level=logging.INFO)

# =========================
# HELPERS
# =========================
def to_cents(amount: float | str) -> str:
    """
    TL tutarını kuruş cinsinden string'e çevirir.
    Örnekler:
      - 349.90 -> '34990'
      - 100.0  -> '10000'
      - 100    -> '10000'
      - '99,99' -> '9999'
    
    Decimal kullanarak kayan nokta hatalarından kaçınır.
    """
    # Virgülü noktaya çevir (Türkçe format desteği)
    s = str(amount).replace(",", ".")
    
    # Decimal ile hassas hesaplama
    d = Decimal(s)
    
    # TL'yi kuruşa çevir (100 ile çarp)
    cents = d * Decimal("100")
    
    # Tam sayıya yuvarla ve string olarak döndür
    return str(cents.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def validate_payment_amount(payment_amount: float, basket: List, tolerance_percent: float = 1.0) -> tuple[bool, str, float, float]:
    """
    Ödeme tutarının sepet toplamı ile tutarlı olduğunu doğrular.
    
    Args:
        payment_amount: Frontend'den gelen ödeme tutarı (TL)
        basket: Sepet öğeleri listesi (BasketItem)
        tolerance_percent: Kabul edilebilir yüzdelik fark (varsayılan %1)
    
    Returns:
        tuple: (is_valid, message, basket_total, difference_percent)
    """
    # Sepet toplamını hesapla
    basket_total = Decimal("0")
    for item in basket:
        price = Decimal(str(item.price))
        qty = Decimal(str(item.quantity))
        basket_total += price * qty
    
    payment_decimal = Decimal(str(payment_amount))
    
    # Farkı hesapla
    if basket_total == Decimal("0"):
        if payment_decimal == Decimal("0"):
            return True, "Sepet ve ödeme tutarı sıfır", float(basket_total), 0.0
        else:
            return False, f"Sepet boş ama ödeme tutarı {payment_amount} TL", float(basket_total), 100.0
    
    difference = abs(payment_decimal - basket_total)
    difference_percent = float((difference / basket_total) * Decimal("100"))
    
    if difference_percent > tolerance_percent:
        return (
            False,
            f"Ödeme tutarı ({payment_amount} TL) ile sepet toplamı ({float(basket_total)} TL) arasında %{difference_percent:.2f} fark var. "
            f"Maksimum izin verilen fark: %{tolerance_percent}",
            float(basket_total),
            difference_percent
        )
    
    return True, "Tutar doğrulandı", float(basket_total), difference_percent

def b64_str(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode("utf-8")

def _hmac_b64(key_str: str, msg_str: str) -> str:
    dig = hmac.new(key_str.encode("utf-8"), msg_str.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(dig).decode("utf-8")


def _parse_fields(raw: Any) -> Dict[str, Any]:
    """
    Aşağıdaki formatların hepsini kabul eder:
      1) Flat dict:            { "merchant_id": "...", ... }
      2) Wrapped dict:         { "fields": { ... } }
      3) Wrapped JSON string:  { "fields": "{ \"merchant_id\":\"...\", ... }" }
      4) Düz JSON string:      "{ \"merchant_id\":\"...\", ... }"
    """
    # 3) ve 4) için (request body doğrudan string ise)
    if isinstance(raw, str):
        return json.loads(raw)

    if isinstance(raw, dict):
        if "fields" in raw:
            val = raw["fields"]
            if isinstance(val, str):
                return json.loads(val)         # JSON string
            elif isinstance(val, dict):
                return val                      # dict
            else:
                raise ValueError("fields must be JSON string or object")
        else:
            return raw                           # flat dict

    raise ValueError("Body must be an object or JSON string")

def _normalize_fields(d: Dict[str, Any]) -> Dict[str, str]:
    """Tüm değerleri stringe çeker, None ise '' yapar, trimler."""
    out: Dict[str, str] = {}
    for k, v in d.items():
        if v is None:
            out[k] = ""
        else:
            out[k] = str(v).strip()
    return out

def client_ip(request: Request, override: Optional[str]) -> str:
    if override:
        return override.split(",")[0].strip()
    for h in ("cf-connecting-ip", "x-real-ip", "x-forwarded-for"):
        v = request.headers.get(h)
        if v:
            return v.split(",")[0].strip()
    return (request.client.host or "").split(",")[0].strip()

# ---- Direct sign orders ----
SIG_ORDER_MINIMAL = [
    "merchant_id","user_ip","merchant_oid","email",
    "payment_amount","payment_type","installment_count",
    "currency","test_mode","non_3d"
]
SIG_ORDER_EXTENDED = [
    "merchant_id","user_ip","merchant_oid","email",
    "payment_amount","payment_type","installment_count",
    "currency","test_mode","non_3d",
    "client_lang","merchant_ok_url","merchant_fail_url",
    "user_name","user_address","user_phone","user_basket"
    # not: bazı hesaplarda card_type da beklenebilir; client_lang'den sonra ekliyoruz (varsa)
]

def _calc_direct_token(fields: Dict[str, str], sign_mode: str = "minimal"):
    """
    Kanonik sıra (Direct API minimal):
    merchant_id, user_ip, merchant_oid, email, payment_amount,
    payment_type, installment_count, currency, test_mode, non_3d, + SALT
    """
    order = [
        "merchant_id", "user_ip", "merchant_oid", "email", "payment_amount",
        "payment_type", "installment_count", "currency", "test_mode", "non_3d"
    ]
    s2s = "".join(fields.get(k, "") for k in order) + PAYTR_MERCHANT_SALT
    calc = _hmac_b64(PAYTR_MERCHANT_KEY, s2s)
    return calc, s2s, order

# ---- iFrame token (get-token) ----
def _calc_iframe_token(*, merchant_id: str, user_ip: str, merchant_oid: str, email: str,
                       payment_amount: str, user_basket: str,
                       no_installment: str, max_installment: str, currency: str, test_mode: str) -> tuple[str, str]:
    # dokümandaki sıra birebir:
    # merchant_id + user_ip + merchant_oid + email + payment_amount + user_basket + no_installment + max_installment + currency + test_mode
    hash_bytes = (merchant_id + user_ip + merchant_oid + email + payment_amount +
                  user_basket + no_installment + max_installment + currency + test_mode).encode("utf-8")
    msg = hash_bytes + PAYTR_MERCHANT_SALT.encode("utf-8")
    dig = hmac.new(PAYTR_MERCHANT_KEY.encode("utf-8"), msg, hashlib.sha256).digest()
    token = base64.b64encode(dig).decode("utf-8")
    return token, (hash_bytes.decode("utf-8") + PAYTR_MERCHANT_SALT)

# =========================
# SCHEMAS
# =========================
class BasketItem(BaseModel):
    name: str = Field(..., max_length=100)
    price: float
    quantity: int = Field(..., ge=1)

class DirectInitIn(BaseModel):
    merchant_oid: str = Field(..., max_length=64)
    email: EmailStr
    payment_amount: float
    payment_type: Literal["card", "card_points"] = "card"
    installment_count: int = Field(0, ge=0, le=12)  # 0=tek çekim, örn 6=6 taksit
    currency: str = "TL"
    non_3d: Literal[0, 1] = 0
    client_lang: Literal["tr", "en"] = "tr"
    user_name: str = Field(..., max_length=60)
    user_address: str = Field(..., max_length=400)
    user_phone: str = Field(..., max_length=20)
    basket: List[BasketItem]
    card_type: Optional[Literal["advantage","axess","combo","bonus","cardfinans","maximum","paraf","world","saglamkart"]] = None
    user_ip: Optional[str] = None
    debug_on: Literal[0, 1] = 1

    @validator("merchant_oid")
    def _oid_alnum(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9]+", v or ""):
            raise ValueError("merchant_oid must be alphanumeric")
        return v

    @validator("currency")
    def _norm_currency(cls, v: str) -> str:
        return "TL" if v in ("", "TRY", "TL") else v

class DirectInitOut(BaseModel):
    action: str
    fields: Dict[str, str]

class VerifyOut(BaseModel):
    match: bool
    mid_match: bool
    calc_token: str
    posted_token: str
    sign_mode: str
    string_to_sign: str
    order_used: List[str]
    notes: Optional[List[str]] = None

# iFrame init input
class IframeInitIn(BaseModel):
    merchant_oid: str = Field(..., max_length=64, pattern=r"^[A-Za-z0-9]+$")
    email: EmailStr
    payment_amount: float           # TL (örn 15.00)
    user_name: str
    user_address: str
    user_phone: str
    basket: List[BasketItem]
    user_ip: Optional[str] = None
    debug_on: int = 1
    no_installment: int = 0         # 0: taksit göster, 1: sadece tek çekim
    max_installment: int = 0        # 0: PayTR azamiye kadar, yoksa 2..12
    currency: str = "TL"

# =========================
# DIRECT — init (taksit: installment_count ile)
# =========================
@router.post("/direct/init", response_model=DirectInitOut)
async def paytr_direct_init(body: DirectInitIn, request: Request):
    ip = client_ip(request, body.user_ip)
    if not ip:
        raise HTTPException(400, "user_ip required")

    # DEBUG: Frontend'den gelen değerleri logla
    log.info("=" * 80)
    log.info("PAYTR_DIRECT_INIT_DEBUG - Frontend'den Gelen Veriler")
    log.info("=" * 80)
    log.info("payment_amount (Frontend gönderdi): %.2f", body.payment_amount)
    log.info("Sepet içeriği:")
    for idx, item in enumerate(body.basket, 1):
        log.info("  %d. %s: %.2f x %d = %.2f", idx, item.name, item.price, item.quantity, item.price * item.quantity)
    log.info("=" * 80)

    # Tutar doğrulama - sepet toplamı ile payment_amount karşılaştır
    is_valid, validation_msg, basket_total, diff_percent = validate_payment_amount(
        body.payment_amount, body.basket, tolerance_percent=1.0
    )
    
    if not is_valid:
        log.error(
            "PAYMENT_VALIDATION_FAILED oid=%s payment_amount=%.2f basket_total=%.2f diff=%.2f%% msg=%s",
            body.merchant_oid, body.payment_amount, basket_total, diff_percent, validation_msg
        )
        raise HTTPException(
            400, 
            f"Ödeme tutarı doğrulaması başarısız: {validation_msg}"
        )
    
    log.info(
        "PAYMENT_VALIDATION_OK oid=%s payment_amount=%.2f basket_total=%.2f diff=%.2f%%",
        body.merchant_oid, body.payment_amount, basket_total, diff_percent
    )

    # Sepet -> Base64(JSON)
    # PayTR dokümantasyonu: sepet fiyatları TL cinsinden 2 ondalıklı string olmalı
    # Örnek: [["Ürün Adı", "10.00", 1]] -> 10 TL
    # NOT: payment_amount kuruş, ama basket TL cinsinden!
    basket_arr = [[i.name, f"{i.price:.2f}", i.quantity] for i in body.basket]
    user_basket_b64 = b64_str(json.dumps(basket_arr, ensure_ascii=False, separators=(",", ":")))

    # PayTR Direct API için payment_amount - TL cinsinden (kuruş DEĞİL!)
    # PayTR form-post TL bekliyor: 10 TL = "1000" kuruş değil, "10" TL
    amount_kurus = to_cents(body.payment_amount)  # Referans için
    amount_tl = str(int(body.payment_amount)) if body.payment_amount == int(body.payment_amount) else f"{body.payment_amount:.2f}"
    
    # DEBUG: Değerleri logla
    log.info("=" * 60)
    log.info("PAYTR PAYMENT_AMOUNT DEBUG")
    log.info("Frontend TL: %.2f", body.payment_amount)
    log.info("PayTR'ye gönderilen (TL): %s", amount_tl)
    log.info("=" * 60)

    fields: Dict[str, str] = {
        "merchant_id": PAYTR_MERCHANT_ID,
        "user_ip": ip,
        "merchant_oid": body.merchant_oid,
        "email": body.email,
        "payment_type": body.payment_type,
        "payment_amount": amount_tl,  # TL cinsinden (10 TL = "10")
        "currency": body.currency,
        "test_mode": PAYTR_TEST_MODE,
        "non_3d": str(body.non_3d),
        "client_lang": body.client_lang,
        "merchant_ok_url": PAYTR_OK_URL,
        "merchant_fail_url": PAYTR_FAIL_URL,
        "user_name": body.user_name,
        "user_address": body.user_address,
        "user_phone": body.user_phone,
        "user_basket": user_basket_b64,
        "installment_count": str(body.installment_count),
        "debug_on": str(body.debug_on),
    }
    if body.card_type:
        fields["card_type"] = body.card_type  # extended modda imzaya dahil ediyoruz

    token, s2s, order = _calc_direct_token(fields, PAYTR_SIGN_MODE)
    fields["paytr_token"] = token

    # Debug logging
    if fields.get("debug_on") == "1":
        log.info("=" * 60)
        log.info("PAYTR DIRECT INIT - Ödeme Detayları")
        log.info("=" * 60)
        log.info("Sipariş ID (merchant_oid): %s", body.merchant_oid)
        log.info("Gelen Tutar (TL): %.2f TL", body.payment_amount)
        log.info("Sepet Toplamı (TL): %.2f TL", basket_total)
        log.info("PayTR'ye Gönderilen: %s TL", amount_tl)
        log.info("-" * 40)
        log.info("Taksit Sayısı: %s", body.installment_count)
        log.info("Kullanıcı IP: %s", ip)
        log.info("Test Modu: %s", PAYTR_TEST_MODE)
        log.info("-" * 40)
        log.info("Sepet İçeriği:")
        for idx, item in enumerate(body.basket, 1):
            item_total = item.price * item.quantity
            log.info("  %d. %s: %.2f TL x %d = %.2f TL", 
                     idx, item.name, item.price, item.quantity, item_total)
        log.info("-" * 40)
        log.info("HMAC Token: %s", token[:20] + "...")
        log.info("=" * 60)

    return DirectInitOut(action="https://www.paytr.com/odeme", fields=fields)

# =========================
# DIRECT — verify (token diff'i gör)
# =========================
@router.post("/direct/verify", response_model=VerifyOut)
async def paytr_direct_verify(raw: Any = Body(...)):
    """
    INIT'ten gelen fields ile imzayı doğrular.
    - Hem flat dict, hem {"fields": {...}} hem de JSON string kabul eder.
    - Eksik/hatalı alanları 'notes' içinde bildirir.
    """
    try:
        parsed = _parse_fields(raw)
    except Exception as e:
        # body anlaşılamadı
        raise HTTPException(status_code=400, detail=f"invalid body: {e}")

    f = _normalize_fields(parsed)

    notes: List[str] = []

    # Bazı hızlı kontroller
    amt = f.get("payment_amount", "")
    if not amt.isdigit():
        notes.append("payment_amount kuruş string olmalı (ör: 15 TL -> '1500').")

    oid = f.get("merchant_oid", "")
    if not re.fullmatch(r"[A-Za-z0-9]+", oid or ""):
        notes.append("merchant_oid sadece alfanümerik olmalı.")

    # Sepet decode (bilgi amaçlı)
    if "user_basket" in f and f["user_basket"]:
        try:
            jb = base64.b64decode(f["user_basket"])
            _ = json.loads(jb.decode("utf-8"))
        except Exception:
            notes.append("user_basket Base64(JSON) çözümlenemedi.")

    # Token üret ve karşılaştır
    calc, s2s, order = _calc_direct_token(f, PAYTR_SIGN_MODE)
    posted = f.get("paytr_token", "")

    mid_match = (f.get("merchant_id", "") == PAYTR_MERCHANT_ID)
    match = (calc == posted) and mid_match

    return VerifyOut(
        match=match,
        mid_match=mid_match,
        calc_token=calc,
        posted_token=posted,
        sign_mode=PAYTR_SIGN_MODE,
        string_to_sign=s2s,
        order_used=order + ["+SALT"],
        notes=notes or None
    )

# =========================
# iFRAME — init (taksiti kullanıcı seçer)
# =========================
@router.post("/iframe/init")
async def paytr_iframe_init(body: IframeInitIn, request: Request):
    ip = body.user_ip or client_ip(request, None)
    if not ip: raise HTTPException(400, "user_ip required")

    # Tutar doğrulama - sepet toplamı ile payment_amount karşılaştır
    is_valid, validation_msg, basket_total, diff_percent = validate_payment_amount(
        body.payment_amount, body.basket, tolerance_percent=1.0
    )
    
    if not is_valid:
        log.error(
            "IFRAME_PAYMENT_VALIDATION_FAILED oid=%s payment_amount=%.2f basket_total=%.2f diff=%.2f%% msg=%s",
            body.merchant_oid, body.payment_amount, basket_total, diff_percent, validation_msg
        )
        raise HTTPException(
            400, 
            f"Ödeme tutarı doğrulaması başarısız: {validation_msg}"
        )

    # PayTR iFrame API için payment_amount - TL cinsinden
    amount_tl = str(int(body.payment_amount)) if body.payment_amount == int(body.payment_amount) else f"{body.payment_amount:.2f}"
    # PayTR sepet fiyatları TL cinsinden 2 ondalıklı string
    basket = [[i.name, f"{i.price:.2f}", i.quantity] for i in body.basket]
    user_basket = b64_str(json.dumps(basket, ensure_ascii=False, separators=(",",":")))

    # Debug logging
    if body.debug_on == 1:
        log.info("=" * 60)
        log.info("PAYTR IFRAME INIT - Ödeme Detayları")
        log.info("=" * 60)
        log.info("Sipariş ID (merchant_oid): %s", body.merchant_oid)
        log.info("Gelen Tutar (TL): %.2f TL", body.payment_amount)
        log.info("Sepet Toplamı (TL): %.2f TL", basket_total)
        log.info("PayTR'ye Gönderilen: %s TL", amount_tl)
        log.info("Max Taksit: %s", body.max_installment)
        log.info("Taksit Yok: %s", body.no_installment)
        log.info("=" * 60)

    token, s2s = _calc_iframe_token(
        merchant_id=PAYTR_MERCHANT_ID,
        user_ip=ip,
        merchant_oid=body.merchant_oid,
        email=body.email,
        payment_amount=amount_tl,
        user_basket=user_basket,
        no_installment=str(body.no_installment),
        max_installment=str(body.max_installment),
        currency=body.currency,
        test_mode=PAYTR_TEST_MODE
    )

    form = {
        "merchant_id": PAYTR_MERCHANT_ID,
        "user_ip": ip,
        "merchant_oid": body.merchant_oid,
        "email": body.email,
        "payment_amount": amount_tl,
        "paytr_token": token,
        "user_basket": user_basket,
        "debug_on": str(body.debug_on),
        "no_installment": str(body.no_installment),
        "max_installment": str(body.max_installment),
        "user_name": body.user_name,
        "user_address": body.user_address,
        "user_phone": body.user_phone,
        "merchant_ok_url": PAYTR_OK_URL,
        "merchant_fail_url": PAYTR_FAIL_URL,
        "timeout_limit": "30",
        "currency": body.currency,
        "test_mode": PAYTR_TEST_MODE
    }

    async with httpx.AsyncClient(timeout=15.0) as cli:
        r = await cli.post("https://www.paytr.com/odeme/api/get-token", data=form)
    try:
        data = r.json()
    except Exception:
        raise HTTPException(502, f"invalid response from get-token: {r.text[:200]}")

    if data.get("status") != "success":
        log.error("IFRAME get-token failed: %s | str2sign=%s", data, s2s)
        raise HTTPException(502, f"get-token failed: {data}")
    return {"token": data["token"]}

# =========================
# CALLBACK — PayTR bildirimi
# =========================
@router.post("/callback", response_class=PlainTextResponse)
async def paytr_callback(
    merchant_oid: str = Form(...),
    status: str       = Form(...),    # "success" | "failed"
    total_amount: str = Form(...),    # örn "34990" (kuruş string)
    hash: str         = Form(...),
):
    log.info("PAYTR_CALLBACK_RECEIVED: oid=%s status=%s total_amount=%s", merchant_oid, status, total_amount)
    
    # 1) İmza doğrulama
    msg = f"{merchant_oid}{PAYTR_MERCHANT_SALT}{status}{total_amount}"
    expected = _hmac_b64(PAYTR_MERCHANT_KEY, msg)
    if expected != hash:
        log.error("PAYTR_CALLBACK_HASH_MISMATCH: oid=%s expected=%s got=%s", merchant_oid, expected[:20], hash[:20] if hash else "None")
        return PlainTextResponse("ERR", status_code=400)

    # 2) İlgili siparişi getir (merchant_oid = order_id)
    ref = db.collection("orders").document(merchant_oid)
    snap = ref.get()
    if not snap.exists:
        return PlainTextResponse("ERR", status_code=404)

    now = datetime.now(timezone.utc)
    doc = snap.to_dict() or {}
    user_id = doc.get("user_id")

    # 3) Idempotent transaction ile payment.status yaz
    @firestore.transactional
    def _txn(tx):
        s = ref.get(transaction=tx)
        d = s.to_dict() or {}
        pay = d.get("payment") or {}
        cur = (pay.get("status") or "").lower()

        # Zaten finalize ise (succeeded/failed) tekrar yazma (idempotent)
        if cur in ("succeeded", "failed"):
            return d

        new_status = "succeeded" if status == "success" else "failed"
        update: Dict[str, Any] = {
            "updated_at": now,
            "payment": {
                **pay,
                "status": new_status,
                "provider": pay.get("provider") or "PAYTR",
                "merchant_oid": merchant_oid,
                "total_amount": total_amount,  # kuruş string
                "raw": {"status": status, "total_amount": total_amount},
            },
        }

        # Başarısız ödemede siparişi iptal etmek istiyorsanız:
        if new_status == "failed":
            update["status"] = "canceled"
            update["status_history"] = firestore.ArrayUnion([
                {"status": "canceled", "at": now, "by": "system", "meta": {"reason": "payment_failed"}}
            ])

        tx.update(ref, update)
        return {**d, **update}

    tx = db.transaction()
    merged = _txn(tx)
    
    log.info("PAYTR_CALLBACK_PROCESSED: oid=%s final_status=%s", merchant_oid, merged.get("payment", {}).get("status"))

    # 4) Ödeme başarılı ise sepeti temizle (isteğe bağlı, yukarıdaki helper ile)
    if status == "success" and user_id:
        _clear_cart(user_id)
        log.info("PAYTR_CALLBACK_CART_CLEARED: user_id=%s", user_id)

    return PlainTextResponse("OK", status_code=200)


# =========================
# TAKSİT ORANLARI
# =========================
@router.get("/installments")
async def installments():
    url = "https://www.paytr.com/odeme/taksit-oranlari"
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(url, data={"merchant_id": PAYTR_MERCHANT_ID})
        r.raise_for_status()
        return r.json()


# =========================
# TOKEN YENİLEME (Taksit değişikliği için)
# =========================
class RefreshTokenIn(BaseModel):
    merchant_oid: str = Field(..., max_length=64, pattern=r"^[A-Za-z0-9]+$")
    installment_count: int = Field(0, ge=0, le=12)
    user_ip: Optional[str] = None


@router.post("/direct/refresh-token", response_model=DirectInitOut)
async def paytr_refresh_token(body: RefreshTokenIn, request: Request):
    """
    Mevcut sipariş için yeni taksit sayısı ile PayTR token yeniler.
    Sipariş oluşturmaz, sadece yeni HMAC token üretir.
    """
    ip = client_ip(request, body.user_ip)
    if not ip:
        raise HTTPException(400, "user_ip required")

    # Siparişi getir
    order_ref = db.collection("orders").document(body.merchant_oid)
    order_snap = order_ref.get()
    if not order_snap.exists:
        raise HTTPException(404, "Sipariş bulunamadı")

    order_data = order_snap.to_dict() or {}
    
    # Sipariş bilgilerini al
    user_id = order_data.get("user_id")
    items = order_data.get("items", [])
    totals = order_data.get("totals", {})
    
    if not items:
        raise HTTPException(400, "Sipariş ürünleri bulunamadı")

    # Kullanıcı bilgilerini al
    user_ref = db.collection("users").document(user_id)
    user_snap = user_ref.get()
    user_data = user_snap.to_dict() if user_snap.exists else {}
    
    email = user_data.get("email", "customer@example.com")
    user_name = user_data.get("name", "Müşteri")
    user_phone = user_data.get("phone", "5000000000")
    
    # Adres bilgisi
    address = order_data.get("shipping_address", {})
    address_parts = [
        address.get("label", ""),
        address.get("street", ""),
        f"No: {address.get('buildingNo', '')}" if address.get("buildingNo") else "",
        address.get("neighborhood", ""),
        address.get("district", ""),
        address.get("city", ""),
    ]
    user_address = ", ".join(p for p in address_parts if p)
    if not user_address:
        user_address = "Adres bilgisi yok"

    # Toplam tutarı hesapla (TL)
    grand_total = totals.get("grandTotal", 0)
    if grand_total == 0:
        # items'dan hesapla
        for item in items:
            price = item.get("price", 0)
            qty = item.get("quantity", 1)
            grand_total += price * qty

    # Basket oluştur
    basket_arr = []
    for item in items:
        name = item.get("title", item.get("name", "Ürün"))
        price = item.get("price", 0)
        qty = item.get("quantity", 1)
        basket_arr.append([name, f"{price:.2f}", qty])
    
    user_basket_b64 = b64_str(json.dumps(basket_arr, ensure_ascii=False, separators=(",", ":")))

    # PayTR TL formatında
    amount_tl = str(int(grand_total)) if grand_total == int(grand_total) else f"{grand_total:.2f}"

    log.info("REFRESH_TOKEN: oid=%s installment=%d amount=%s", 
             body.merchant_oid, body.installment_count, amount_tl)

    fields: Dict[str, str] = {
        "merchant_id": PAYTR_MERCHANT_ID,
        "user_ip": ip,
        "merchant_oid": body.merchant_oid,
        "email": email,
        "payment_type": "card",
        "payment_amount": amount_tl,
        "currency": "TL",
        "test_mode": PAYTR_TEST_MODE,
        "non_3d": "0",
        "client_lang": "tr",
        "merchant_ok_url": PAYTR_OK_URL,
        "merchant_fail_url": PAYTR_FAIL_URL,
        "user_name": user_name,
        "user_address": user_address,
        "user_phone": user_phone,
        "user_basket": user_basket_b64,
        "installment_count": str(body.installment_count),
        "debug_on": "1",
    }

    token, _, _ = _calc_direct_token(fields, PAYTR_SIGN_MODE)
    fields["paytr_token"] = token

    return DirectInitOut(action="https://www.paytr.com/odeme", fields=fields)

# (İsteğe bağlı demo formu tutmak istersen bırak; prod'da gerekli değil)

@router.get("/success", response_class=HTMLResponse)
async def paytr_success():
    return HTMLResponse("""
    <!doctype html><meta charset="utf-8">
    <h1>Ödeme Başarılı</h1>
    <p>Teşekkürler! Ödemeniz alındı.</p>
    """, status_code=200)

@router.get("/fail", response_class=HTMLResponse)
async def paytr_fail():
    return HTMLResponse("""
    <!doctype html><meta charset="utf-8">
    <h1>Ödeme Başarısız</h1>
    <p>İşlem tamamlanamadı. Lütfen tekrar deneyin.</p>
    """, status_code=200)