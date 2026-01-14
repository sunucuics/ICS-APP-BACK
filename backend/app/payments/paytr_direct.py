# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import json
import hmac
import base64
import hashlib
import logging
import re
import time
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Literal, Tuple
from datetime import datetime, timezone

import httpx
from firebase_admin import firestore
from fastapi import APIRouter, HTTPException, Request, Form, Body, Depends
from pydantic import BaseModel, Field, EmailStr, validator
from starlette.responses import PlainTextResponse, HTMLResponse

from backend.app.config import db
from backend.app.core.security import get_current_user

# =========================
# ENV
# =========================

INSTALLMENT_SURCHARGE_PERCENT = Decimal(os.getenv("INSTALLMENT_SURCHARGE_PERCENT", "15.0"))

PAYTR_MERCHANT_ID = os.getenv("PAYTR_MERCHANT_ID", "")
PAYTR_MERCHANT_KEY = os.getenv("PAYTR_MERCHANT_KEY", "")
PAYTR_MERCHANT_SALT = os.getenv("PAYTR_MERCHANT_SALT", "")

PAYTR_OK_URL = os.getenv("PAYTR_OK_URL", "https://example.com/payment/success")
PAYTR_FAIL_URL = os.getenv("PAYTR_FAIL_URL", "https://example.com/payment/fail")

PAYTR_TEST_MODE = "1" if os.getenv("PAYTR_TEST_MODE", "0").lower() in ("1", "true", "yes") else "0"

if not (PAYTR_MERCHANT_ID and PAYTR_MERCHANT_KEY and PAYTR_MERCHANT_SALT):
    raise RuntimeError("PAYTR env missing")

_PREFIX = (os.getenv("FIREBASE_COLLECTION_PREFIX") or "").strip()
def _prefixed(name: str) -> str:
    return f"{_PREFIX}{name}" if _PREFIX else name

_CARTS = _prefixed("carts")

router = APIRouter(prefix="/paytr", tags=["paytr"])

log = logging.getLogger("paytr")
if not log.handlers:
    logging.basicConfig(level=logging.INFO)

_DEC_2 = Decimal("0.01")

PAYTR_BIN_DETAIL_URL = "https://www.paytr.com/odeme/api/bin-detail"
PAYTR_INSTALLMENT_RATES_URL = "https://www.paytr.com/odeme/taksit-oranlari"
PAYTR_IFRAME_GET_TOKEN_URL = "https://www.paytr.com/odeme/api/get-token"

# =========================
# HELPERS
# =========================

def _d(x: Any) -> Decimal:
    s = str(x).strip().replace(",", ".")
    return Decimal(s)

def tl_str(amount_tl: Decimal) -> str:
    q = amount_tl.quantize(_DEC_2, rounding=ROUND_HALF_UP)
    return format(q, "f")

def to_kurus_str(amount_tl: Decimal) -> str:
    kurus = (amount_tl * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return str(kurus)

def b64_str(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode("utf-8")

def _hmac_b64(key_str: str, msg_str: str) -> str:
    dig = hmac.new(key_str.encode("utf-8"), msg_str.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(dig).decode("utf-8")

def client_ip(request: Request, override: Optional[str]) -> str:
    if override:
        return override.split(",")[0].strip()
    for h in ("cf-connecting-ip", "x-real-ip", "x-forwarded-for"):
        v = request.headers.get(h)
        if v:
            return v.split(",")[0].strip()
    return (request.client.host or "").split(",")[0].strip()

def _clear_cart(user_id: str) -> None:
    try:
        cref = db.collection(_CARTS).document(user_id)
        cref.set({"items": []}, merge=True)
        for dsnap in cref.collection("items").stream():
            dsnap.reference.delete()
    except Exception:
        pass

def basket_total_tl(basket: List["BasketItem"]) -> Decimal:
    total = Decimal("0")
    for it in basket:
        total += _d(it.price) * Decimal(str(it.quantity))
    return total.quantize(_DEC_2, rounding=ROUND_HALF_UP)

def apply_installment_rate(base_amount_tl: Decimal, rate_percent: Decimal) -> Decimal:
    multiplier = Decimal("1") + (rate_percent / Decimal("100"))
    return (base_amount_tl * multiplier).quantize(_DEC_2, rounding=ROUND_HALF_UP)

def normalize_and_validate_client_amount(
    client_amount: Decimal,
    basket_total: Decimal,
    tolerance_percent: Decimal = Decimal("1.0"),
) -> Tuple[bool, str, Decimal]:
    """
    Frontend bazen TL bazen kuruş(x100) gönderebilir.
    Tahsil edilecek tutar HER ZAMAN basket_total üzerinden belirlenir.
    Bu fonksiyon sadece "bariz manipulasyon" var mı diye kontrol eder
    ve unit mismatch varsa TL’ye normalize eder (log amaçlı).
    """
    if basket_total <= 0:
        return False, "Sepet toplamı 0 veya negatif olamaz", basket_total

    # TL gibi mi?
    diff = abs(client_amount - basket_total)
    diff_pct = (diff / basket_total) * Decimal("100")
    if diff_pct <= tolerance_percent:
        return True, "Client amount TL olarak uyumlu", client_amount

    # Kuruş(x100) gibi mi?
    client_div_100 = (client_amount / Decimal("100")).quantize(_DEC_2, rounding=ROUND_HALF_UP)
    diff2 = abs(client_div_100 - basket_total)
    diff2_pct = (diff2 / basket_total) * Decimal("100")
    if diff2_pct <= tolerance_percent:
        return True, "Client amount kuruş(x100) gibi; TL'ye normalize edildi", client_div_100

    return False, f"Tutar uyuşmaz: client={client_amount} basket={basket_total}", client_amount

# ---- Direct token order (minimal canonical order) ----
_DIRECT_SIG_ORDER = [
    "merchant_id",
    "user_ip",
    "merchant_oid",
    "email",
    "payment_amount",
    "payment_type",
    "installment_count",
    "currency",
    "test_mode",
    "non_3d",
]

def _calc_direct_token(fields: Dict[str, str]) -> Tuple[str, str, List[str]]:
    s2s = "".join(fields.get(k, "") for k in _DIRECT_SIG_ORDER) + PAYTR_MERCHANT_SALT
    token = _hmac_b64(PAYTR_MERCHANT_KEY, s2s)
    return token, s2s, list(_DIRECT_SIG_ORDER)

# ---- iFrame token (get-token) ----
def _calc_iframe_token(
    *,
    merchant_id: str,
    user_ip: str,
    merchant_oid: str,
    email: str,
    payment_amount_kurus: str,
    user_basket: str,
    no_installment: str,
    max_installment: str,
    currency: str,
    test_mode: str,
) -> Tuple[str, str]:
    raw = (
        merchant_id
        + user_ip
        + merchant_oid
        + email
        + payment_amount_kurus
        + user_basket
        + no_installment
        + max_installment
        + currency
        + test_mode
    )
    msg = raw + PAYTR_MERCHANT_SALT
    dig = hmac.new(PAYTR_MERCHANT_KEY.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(dig).decode("utf-8"), msg

# ---- PayTR service calls ----
async def paytr_bin_detail(bin_number: str) -> Dict[str, Any]:
    bn = (bin_number or "").strip()
    if not re.fullmatch(r"\d{6}|\d{8}", bn):
        raise HTTPException(400, "bin_number 6 veya 8 hane olmalı")

    hash_str = bn + PAYTR_MERCHANT_ID + PAYTR_MERCHANT_SALT
    token = base64.b64encode(
        hmac.new(PAYTR_MERCHANT_KEY.encode("utf-8"), hash_str.encode("utf-8"), hashlib.sha256).digest()
    ).decode("utf-8")

    data = {"merchant_id": PAYTR_MERCHANT_ID, "bin_number": bn, "paytr_token": token}

    async with httpx.AsyncClient(timeout=20.0) as cli:
        r = await cli.post(PAYTR_BIN_DETAIL_URL, data=data)

    try:
        res = r.json()
    except Exception:
        raise HTTPException(502, f"BIN detail invalid response: {r.text[:200]}")

    return res

async def paytr_installment_rates(single_ratio: int = 1, abroad_ratio: int = 0) -> Dict[str, Any]:
    request_id = str(int(time.time() * 1000))
    hash_str = PAYTR_MERCHANT_ID + request_id + PAYTR_MERCHANT_SALT
    token = base64.b64encode(
        hmac.new(PAYTR_MERCHANT_KEY.encode("utf-8"), hash_str.encode("utf-8"), hashlib.sha256).digest()
    ).decode("utf-8")

    data = {
        "merchant_id": PAYTR_MERCHANT_ID,
        "request_id": request_id,
        "paytr_token": token,
        "single_ratio": str(int(bool(single_ratio))),
        "abroad_ratio": str(int(bool(abroad_ratio))),
    }

    async with httpx.AsyncClient(timeout=30.0) as cli:
        r = await cli.post(PAYTR_INSTALLMENT_RATES_URL, data=data)

    try:
        res = r.json()
    except Exception:
        raise HTTPException(502, f"Installment rates invalid response: {r.text[:200]}")

    return res

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
    payment_amount: float = 0  # client gönderiyor; backend güvenmez
    payment_type: Literal["card", "card_points"] = "card"
    installment_count: Literal[0, 3] = 0  # sadece 0 veya 3
    currency: str = "TL"
    non_3d: Literal[0, 1] = 0
    client_lang: Literal["tr", "en"] = "tr"
    user_name: str = Field(..., max_length=60)
    user_address: str = Field(..., max_length=400)
    user_phone: str = Field(..., max_length=20)
    basket: List[BasketItem]
    bin_number: Optional[str] = None  # 3 taksit için zorunlu
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
    string_to_sign: str
    order_used: List[str]
    notes: Optional[List[str]] = None

class BinDetailIn(BaseModel):
    bin_number: str = Field(..., description="Kartın ilk 6 veya 8 hanesi")
    debug_on: int = 0

class InstallmentQuoteIn(BaseModel):
    bin_number: str = Field(..., description="Kartın ilk 6 veya 8 hanesi")
    amount_tl: float = Field(..., gt=0, description="Peşin fiyat (TL)")

class IframeInitIn(BaseModel):
    merchant_oid: str = Field(..., max_length=64, pattern=r"^[A-Za-z0-9]+$")
    email: EmailStr
    payment_amount: float  # client gönderiyor; backend basket'a göre belirleyecek
    user_name: str
    user_address: str
    user_phone: str
    basket: List[BasketItem]
    user_ip: Optional[str] = None
    debug_on: int = 1
    no_installment: int = 0
    max_installment: int = 0
    currency: str = "TL"

class RefreshTokenIn(BaseModel):
    merchant_oid: str = Field(..., max_length=64, pattern=r"^[A-Za-z0-9]+$")
    installment_count: Literal[0, 3] = 0
    user_ip: Optional[str] = None
    bin_number: Optional[str] = None  # 3 taksit refresh için zorunlu

# =========================
# DIRECT — BIN DETAIL
# =========================
@router.post("/direct/bin-detail")
async def direct_bin_detail(body: BinDetailIn):
    res = await paytr_bin_detail(body.bin_number)
    if body.debug_on:
        log.info("BIN_DETAIL: %s", res)
    return res

# =========================
# DIRECT — INSTALLMENT QUOTE (peşin / 3 taksit)
# =========================
@router.post("/direct/installment-quote")
async def direct_installment_quote(body: InstallmentQuoteIn):
    base_amount = _d(body.amount_tl).quantize(_DEC_2, rounding=ROUND_HALF_UP)

    # Debug log: BIN sorgusu başlatılıyor
    log.info("BIN_DETAIL_REQUEST bin=%s amount=%s test_mode=%s", body.bin_number, base_amount, PAYTR_TEST_MODE)

    bres = await paytr_bin_detail(body.bin_number)
    
    # Debug log: PayTR'den gelen yanıt
    log.info("BIN_DETAIL_RESPONSE bin=%s response=%s", body.bin_number, bres)
    
    if bres.get("status") != "success":
        log.warning("BIN_DETAIL_FAILED bin=%s reason=%s", body.bin_number, bres.get("err_msg") or bres.get("reason"))
        return {
            "status": "failed",
            "reason": bres.get("err_msg") or bres.get("reason") or "BIN tanımsız",
            "installments": [
                {
                    "installment_count": 0,
                    "rate_percent": 0,
                    "total_tl": tl_str(base_amount),
                    "per_installment_tl": tl_str(base_amount),
                }
            ],
            "bin": bres,
        }

    card_type = (bres.get("cardType") or "").lower()  # credit / debit
    brand = (bres.get("brand") or "").lower()

    # Debug log: Parse edilen değerler
    log.info("BIN_DETAIL_PARSED bin=%s cardType=%s brand=%s", body.bin_number, card_type, brand)

    options: List[Dict[str, Any]] = [
        {"installment_count": 0, "rate_percent": 0, "total_tl": tl_str(base_amount), "per_installment_tl": tl_str(base_amount)}
    ]

    if card_type != "credit" or not brand or brand == "none":
        log.info("BIN_NO_INSTALLMENT bin=%s reason=cardType=%s brand=%s", body.bin_number, card_type, brand)
        return {"status": "success", "brand": brand, "cardType": card_type, "installments": options, "bin": bres}

    total = apply_installment_rate(base_amount, INSTALLMENT_SURCHARGE_PERCENT)
    per = (total / Decimal("3")).quantize(_DEC_2, rounding=ROUND_HALF_UP)

    options.append(
        {
            "installment_count": 3,
            "rate_percent": float(INSTALLMENT_SURCHARGE_PERCENT),
            "total_tl": tl_str(total),
            "per_installment_tl": tl_str(per),
        }
    )

    log.info("BIN_INSTALLMENT_ADDED bin=%s cardType=%s brand=%s installments=%s", 
             body.bin_number, card_type, brand, len(options))

    return {"status": "success", "brand": brand, "cardType": card_type, "installments": options, "bin": bres}

# =========================
# DIRECT — init (0 / 3)
# =========================
@router.post("/direct/init", response_model=DirectInitOut)
async def paytr_direct_init(
    body: DirectInitIn, 
    request: Request,
    current_user: dict = Depends(get_current_user)
):
    user_id = current_user.get("id")
    if not user_id:
        raise HTTPException(401, "Kullanıcı kimliği alınamadı")
    
    ip = client_ip(request, body.user_ip)
    if not ip:
        raise HTTPException(400, "user_ip required")

    base_total = basket_total_tl(body.basket)

    # client amount sanity check (opsiyonel)
    client_amount = _d(body.payment_amount)
    ok, msg, normalized_client = normalize_and_validate_client_amount(client_amount, base_total, tolerance_percent=Decimal("1.0"))
    if not ok and body.payment_amount not in (0, 0.0):
        log.error("PAYMENT_AMOUNT_MISMATCH oid=%s msg=%s client=%s basket=%s", body.merchant_oid, msg, client_amount, base_total)
        raise HTTPException(400, f"Ödeme tutarı doğrulaması başarısız: {msg}")

    if msg.startswith("Client amount kuruş"):
        log.warning("AMOUNT_UNIT_NORMALIZED oid=%s client=%s normalized=%s basket=%s", body.merchant_oid, client_amount, normalized_client, base_total)

    basket_arr: List[List[Any]] = [
        [i.name, tl_str(_d(i.price).quantize(_DEC_2, rounding=ROUND_HALF_UP)), i.quantity]
        for i in body.basket
    ]

    payable_total = base_total
    resolved_brand: Optional[str] = None

    if body.installment_count == 3:
        if not body.bin_number:
            raise HTTPException(400, "3 taksit için bin_number zorunludur.")

        bres = await paytr_bin_detail(body.bin_number)
        if bres.get("status") != "success":
            raise HTTPException(400, f"BIN doğrulanamadı: {bres.get('err_msg') or bres}")

        card_type = (bres.get("cardType") or "").lower()
        brand = (bres.get("brand") or "").lower()

        if card_type != "credit":
            raise HTTPException(400, "Banka kartı (debit) ile 3 taksit yapılamaz.")
        if not brand or brand == "none":
            raise HTTPException(400, "Bu kart taksit programına uygun değil (brand=none).")

        resolved_brand = brand

        payable_total = apply_installment_rate(base_total, INSTALLMENT_SURCHARGE_PERCENT)

        surcharge = (payable_total - base_total).quantize(_DEC_2, rounding=ROUND_HALF_UP)
        if surcharge > 0:
            basket_arr.append([f"3 Taksit Vade Farkı (%{tl_str(INSTALLMENT_SURCHARGE_PERCENT)})", tl_str(surcharge), 1])

    user_basket_b64 = b64_str(json.dumps(basket_arr, ensure_ascii=False, separators=(",", ":")))
    amount_tl = tl_str(payable_total)  # IMPORTANT: Direct'te TL string

    fields: Dict[str, str] = {
        "merchant_id": PAYTR_MERCHANT_ID,
        "user_ip": ip,
        "merchant_oid": body.merchant_oid,
        "email": body.email,
        "payment_type": body.payment_type,
        "payment_amount": amount_tl,
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

    if body.installment_count == 3 and resolved_brand:
        fields["card_type"] = resolved_brand

    token, s2s, order_used = _calc_direct_token(fields)
    fields["paytr_token"] = token

    if fields.get("debug_on") == "1":
        log.info("=" * 80)
        log.info("PAYTR DIRECT INIT")
        log.info("oid=%s ip=%s test=%s", body.merchant_oid, ip, PAYTR_TEST_MODE)
        log.info("base_total=%s TL | payable_total=%s TL | installment=%s | card_type=%s",
                 tl_str(base_total), amount_tl, body.installment_count, fields.get("card_type"))
        log.info("string_to_sign=%s", s2s)
        log.info("order_used=%s", order_used)
        log.info("token=%s...", token[:24])
        log.info("=" * 80)

    # Payment session kaydet (callback'te sipariş oluşturmak için)
    session_data = {
        "merchant_oid": body.merchant_oid,
        "user_id": user_id,
        "email": body.email,
        "basket": [{"name": i.name, "price": float(i.price), "quantity": i.quantity} for i in body.basket],
        "base_amount": float(base_total),
        "payable_amount": float(payable_total),
        "installment_count": body.installment_count,
        "user_name": body.user_name,
        "user_address": body.user_address,
        "user_phone": body.user_phone,
        "user_ip": ip,
        "status": "pending",
        "created_at": datetime.now(timezone.utc),
    }
    db.collection(_prefixed("payment_sessions")).document(body.merchant_oid).set(session_data, merge=True)
    log.info("PAYMENT_SESSION_SAVED oid=%s user=%s", body.merchant_oid, user_id)

    return DirectInitOut(action="https://www.paytr.com/odeme", fields=fields)

# =========================
# DIRECT — verify (token debug)
# =========================
def _parse_fields(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, str):
        return json.loads(raw)
    if isinstance(raw, dict):
        if "fields" in raw:
            val = raw["fields"]
            if isinstance(val, str):
                return json.loads(val)
            if isinstance(val, dict):
                return val
            raise ValueError("fields must be JSON string or object")
        return raw
    raise ValueError("Body must be an object or JSON string")

def _normalize_fields(d: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for k, v in d.items():
        out[k] = "" if v is None else str(v).strip()
    return out

@router.post("/direct/verify", response_model=VerifyOut)
async def paytr_direct_verify(raw: Any = Body(...)):
    try:
        parsed = _parse_fields(raw)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid body: {e}")

    f = _normalize_fields(parsed)
    notes: List[str] = []

    oid = f.get("merchant_oid", "")
    if not re.fullmatch(r"[A-Za-z0-9]+", oid or ""):
        notes.append("merchant_oid sadece alfanümerik olmalı.")

    amt = f.get("payment_amount", "")
    if not re.fullmatch(r"\d+(\.\d{1,2})?", amt or ""):
        notes.append("payment_amount Direct için TL decimal olmalı (örn: 100.99).")

    if f.get("user_basket"):
        try:
            jb = base64.b64decode(f["user_basket"])
            _ = json.loads(jb.decode("utf-8"))
        except Exception:
            notes.append("user_basket Base64(JSON) çözümlenemedi.")

    calc, s2s, order = _calc_direct_token(f)
    posted = f.get("paytr_token", "")

    mid_match = (f.get("merchant_id", "") == PAYTR_MERCHANT_ID)
    match = (calc == posted) and mid_match

    return VerifyOut(
        match=match,
        mid_match=mid_match,
        calc_token=calc,
        posted_token=posted,
        string_to_sign=s2s,
        order_used=order + ["+SALT"],
        notes=notes or None,
    )

# =========================
# DIRECT — refresh-token (siparişten yeniden token)
# =========================
@router.post("/direct/refresh-token", response_model=DirectInitOut)
async def paytr_refresh_token(body: RefreshTokenIn, request: Request):
    ip = client_ip(request, body.user_ip)
    if not ip:
        raise HTTPException(400, "user_ip required")

    order_ref = db.collection("orders").document(body.merchant_oid)
    order_snap = order_ref.get()
    if not order_snap.exists:
        raise HTTPException(404, "Sipariş bulunamadı")

    order_data = order_snap.to_dict() or {}
    user_id = order_data.get("user_id")
    items = order_data.get("items", []) or []
    totals = (order_data.get("totals") or {}) if isinstance(order_data.get("totals"), dict) else {}

    if not items:
        raise HTTPException(400, "Sipariş ürünleri bulunamadı")

    # user
    user_data: Dict[str, Any] = {}
    if user_id:
        uref = db.collection("users").document(user_id)
        usnap = uref.get()
        if usnap.exists:
            user_data = usnap.to_dict() or {}

    email = user_data.get("email", "customer@example.com")
    user_name = user_data.get("name", "Müşteri")
    user_phone = user_data.get("phone", "5000000000")

    address = order_data.get("shipping_address", {}) or {}
    address_parts = [
        address.get("label", ""),
        address.get("street", ""),
        f"No: {address.get('buildingNo', '')}" if address.get("buildingNo") else "",
        address.get("neighborhood", ""),
        address.get("district", ""),
        address.get("city", ""),
    ]
    user_address = ", ".join(p for p in address_parts if p) or "Adres bilgisi yok"

    # base total (TL)
    base_total = _d(totals.get("grandTotal", 0)).quantize(_DEC_2, rounding=ROUND_HALF_UP)
    if base_total <= 0:
        t = Decimal("0")
        for it in items:
            t += _d(it.get("price", 0)) * Decimal(str(it.get("quantity", 1)))
        base_total = t.quantize(_DEC_2, rounding=ROUND_HALF_UP)

    basket_arr: List[List[Any]] = []
    for it in items:
        name = it.get("title", it.get("name", "Ürün"))
        price = _d(it.get("price", 0)).quantize(_DEC_2, rounding=ROUND_HALF_UP)
        qty = int(it.get("quantity", 1))
        basket_arr.append([name, tl_str(price), qty])

    payable_total = base_total
    resolved_brand: Optional[str] = None

    if body.installment_count == 3:
        if not body.bin_number:
            raise HTTPException(400, "Taksitli refresh-token için bin_number zorunludur.")

        bres = await paytr_bin_detail(body.bin_number)
        if bres.get("status") != "success":
            raise HTTPException(400, f"BIN doğrulanamadı: {bres.get('err_msg') or bres}")

        if (bres.get("cardType") or "").lower() != "credit":
            raise HTTPException(400, "Banka kartı (debit) ile taksit yapılamaz.")

        brand = (bres.get("brand") or "").lower()
        if not brand or brand == "none":
            raise HTTPException(400, "Bu kart program ortaklığına dahil değil (brand=none); taksit yapılamaz.")

        resolved_brand = brand
        payable_total = apply_installment_rate(base_total, INSTALLMENT_SURCHARGE_PERCENT)

        surcharge = (payable_total - base_total).quantize(_DEC_2, rounding=ROUND_HALF_UP)
        if surcharge > 0:
            basket_arr.append([f"3 Taksit Vade Farkı (%{tl_str(INSTALLMENT_SURCHARGE_PERCENT)})", tl_str(surcharge), 1])

    user_basket_b64 = b64_str(json.dumps(basket_arr, ensure_ascii=False, separators=(",", ":")))

    fields: Dict[str, str] = {
        "merchant_id": PAYTR_MERCHANT_ID,
        "user_ip": ip,
        "merchant_oid": body.merchant_oid,
        "email": email,
        "payment_type": "card",
        "payment_amount": tl_str(payable_total),
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

    if body.installment_count == 3 and resolved_brand:
        fields["card_type"] = resolved_brand

    token, _, _ = _calc_direct_token(fields)
    fields["paytr_token"] = token

    log.info("REFRESH_TOKEN oid=%s installment=%s amount=%s card_type=%s",
             body.merchant_oid, body.installment_count, fields["payment_amount"], fields.get("card_type"))

    return DirectInitOut(action="https://www.paytr.com/odeme", fields=fields)

# =========================
# iFRAME — init (taksiti PayTR iframe içinde seçer)
# =========================
@router.post("/iframe/init")
async def paytr_iframe_init(body: IframeInitIn, request: Request):
    ip = body.user_ip or client_ip(request, None)
    if not ip:
        raise HTTPException(400, "user_ip required")

    # basket total backend
    b_total = basket_total_tl(body.basket)

    # iframe payment_amount: KURUŞ
    amount_kurus = to_kurus_str(b_total)

    basket = [[i.name, tl_str(_d(i.price).quantize(_DEC_2, rounding=ROUND_HALF_UP)), i.quantity] for i in body.basket]
    user_basket = b64_str(json.dumps(basket, ensure_ascii=False, separators=(",", ":")))

    token, s2s = _calc_iframe_token(
        merchant_id=PAYTR_MERCHANT_ID,
        user_ip=ip,
        merchant_oid=body.merchant_oid,
        email=body.email,
        payment_amount_kurus=amount_kurus,
        user_basket=user_basket,
        no_installment=str(body.no_installment),
        max_installment=str(body.max_installment),
        currency=body.currency,
        test_mode=PAYTR_TEST_MODE,
    )

    form = {
        "merchant_id": PAYTR_MERCHANT_ID,
        "user_ip": ip,
        "merchant_oid": body.merchant_oid,
        "email": body.email,
        "payment_amount": amount_kurus,
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
        "test_mode": PAYTR_TEST_MODE,
    }

    if body.debug_on == 1:
        log.info("=" * 70)
        log.info("PAYTR IFRAME INIT")
        log.info("oid=%s ip=%s basket_total=%s TL payment_amount=%s (kuruş)", body.merchant_oid, ip, tl_str(b_total), amount_kurus)
        log.info("str2sign=%s", s2s)
        log.info("=" * 70)

    async with httpx.AsyncClient(timeout=20.0) as cli:
        r = await cli.post(PAYTR_IFRAME_GET_TOKEN_URL, data=form)

    try:
        data = r.json()
    except Exception:
        raise HTTPException(502, f"invalid response from get-token: {r.text[:200]}")

    if data.get("status") != "success":
        log.error("IFRAME get-token failed: %s | str2sign=%s", data, s2s)
        raise HTTPException(502, f"get-token failed: {data}")

    return {"token": data["token"]}

# =========================
# TAKSİT ORANLARI
# =========================
@router.get("/installments")
async def installments():
    return await paytr_installment_rates(single_ratio=1, abroad_ratio=0)

# =========================
# CALLBACK — PayTR bildirimi
# =========================

@router.api_route("/callback", methods=["POST"], response_class=PlainTextResponse)
@router.api_route("/callback/", methods=["POST"], response_class=PlainTextResponse)  # trailing slash
async def paytr_callback(request: Request):
    form = await request.form()

    merchant_oid = (form.get("merchant_oid") or "").strip()
    cb_status = (form.get("status") or "").strip()
    total_amount = (form.get("total_amount") or "").strip()
    incoming_hash = (form.get("hash") or "").strip()

    log.info("PAYTR_CALLBACK_RECEIVED oid=%s status=%s amount=%s", merchant_oid, cb_status, total_amount)

    # 1) HASH doğrulama
    msg = f"{merchant_oid}{PAYTR_MERCHANT_SALT}{cb_status}{total_amount}"
    expected = _hmac_b64(PAYTR_MERCHANT_KEY, msg)

    if expected != incoming_hash:
        log.error("PAYTR_CALLBACK_HASH_MISMATCH oid=%s", merchant_oid)
        return PlainTextResponse("ERR", status_code=400)

    now = datetime.now(timezone.utc)
    orders_col = db.collection(_prefixed("orders"))
    sessions_col = db.collection(_prefixed("payment_sessions"))

    # 2) Önce sipariş var mı kontrol et
    order_ref = orders_col.document(merchant_oid)
    order_snap = order_ref.get()

    if order_snap.exists:
        # Sipariş zaten var -> sadece güncelle
        doc = order_snap.to_dict() or {}
        user_id = doc.get("user_id")

        @firestore.transactional
        def _update_order(tx):
            s = order_ref.get(transaction=tx)
            d = s.to_dict() or {}
            pay = d.get("payment") or {}
            cur = (pay.get("status") or "").lower()

            if cur in ("succeeded", "failed"):
                return d  # idempotent

            new_status = "succeeded" if cb_status == "success" else "failed"

            update = {
                "updated_at": now,
                "payment": {
                    **pay,
                    "status": new_status,
                    "provider": pay.get("provider") or "PAYTR",
                    "merchant_oid": merchant_oid,
                    "total_amount": total_amount,
                },
            }

            if new_status == "failed":
                update["status"] = "canceled"

            tx.update(order_ref, update)
            return {**d, **update}

        _update_order(db.transaction())
        log.info("PAYTR_CALLBACK_ORDER_UPDATED oid=%s status=%s", merchant_oid, cb_status)

        if cb_status == "success" and user_id:
            _clear_cart(user_id)

        return PlainTextResponse("OK", status_code=200)

    # 3) Sipariş yok -> Payment session'dan oluştur
    session_ref = sessions_col.document(merchant_oid)
    session_snap = session_ref.get()

    if not session_snap.exists:
        log.error("PAYTR_CALLBACK_SESSION_NOT_FOUND oid=%s (OK returning)", merchant_oid)
        return PlainTextResponse("OK", status_code=200)

    session = session_snap.to_dict() or {}
    user_id = session.get("user_id")

    # Siparişi oluştur
    payment_status = "succeeded" if cb_status == "success" else "failed"
    order_status = "confirmed" if cb_status == "success" else "canceled"

    order_data = {
        "id": merchant_oid,
        "user_id": user_id,
        "items": session.get("basket", []),
        "status": order_status,
        "payment": {
            "status": payment_status,
            "provider": "PAYTR",
            "merchant_oid": merchant_oid,
            "total_amount": total_amount,
            "installment_count": session.get("installment_count", 0),
        },
        "totals": {
            "subtotal": session.get("base_amount", 0),
            "grandTotal": session.get("payable_amount", 0),
        },
        "shipping_address": {
            "full_address": session.get("user_address", ""),
        },
        "contact": {
            "name": session.get("user_name", ""),
            "email": session.get("email", ""),
            "phone": session.get("user_phone", ""),
        },
        "created_at": now,
        "updated_at": now,
    }

    order_ref.set(order_data)
    log.info("PAYTR_CALLBACK_ORDER_CREATED oid=%s user=%s status=%s", merchant_oid, user_id, order_status)

    # Session'ı güncelle
    session_ref.update({
        "status": "completed" if cb_status == "success" else "failed",
        "updated_at": now,
    })

    # Başarılıysa sepeti temizle
    if cb_status == "success" and user_id:
        _clear_cart(user_id)
        log.info("PAYTR_CALLBACK_CART_CLEARED user=%s", user_id)

    # 4) Mutlaka sadece OK
    return PlainTextResponse("OK", status_code=200)



@router.api_route("/success", methods=["GET", "POST"], response_class=HTMLResponse)
async def paytr_success(request: Request):
    # İstersen POST form alanlarını da okuyabilirsin:
    # form = await request.form()
    return HTMLResponse(
        """
        <!doctype html><meta charset="utf-8">
        <h1>Ödeme Başarılı</h1>
        <p>Teşekkürler! Ödemeniz alındı.</p>
        """,
        status_code=200,
    )

@router.api_route("/fail", methods=["GET", "POST"], response_class=HTMLResponse)
async def paytr_fail(request: Request):
    # form = await request.form()
    return HTMLResponse(
        """
        <!doctype html><meta charset="utf-8">
        <h1>Ödeme Başarısız</h1>
        <p>İşlem tamamlanamadı. Lütfen tekrar deneyin.</p>
        """,
        status_code=200,
    )
