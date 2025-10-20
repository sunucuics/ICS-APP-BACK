# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import json
import hmac
import base64
import hashlib
import ipaddress
from decimal import Decimal
from typing import List, Optional, Dict, Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field, validator, EmailStr
from firebase_admin import firestore

from backend.app.config import db  # Firestore client (initialize elsewhere)

router = APIRouter(prefix="/paytr", tags=["paytr"])

# =========================== ENV ===========================
PAYTR_MERCHANT_ID = os.getenv("PAYTR_MERCHANT_ID", "")
PAYTR_MERCHANT_KEY = os.getenv("PAYTR_MERCHANT_KEY", "")
PAYTR_MERCHANT_SALT = os.getenv("PAYTR_MERCHANT_SALT", "")
PAYTR_OK_URL = os.getenv("PAYTR_OK_URL", "https://example.com/payment/success")
PAYTR_FAIL_URL = os.getenv("PAYTR_FAIL_URL", "https://example.com/payment/fail")
PAYTR_TEST_MODE = "1" if os.getenv("PAYTR_TEST_MODE", "1") in ("1", "true", "True") else "0"

PAYTR_TOKEN_URL = "https://www.paytr.com/odeme/api/get-token"
PAYTR_IFRAME_URL = "https://www.paytr.com/odeme/guvenli/{}"

# ========================== Schemas =========================
class BasketItem(BaseModel):
    name: str = Field(..., max_length=100)
    price: float  # örn 18.00
    quantity: int = Field(..., ge=1)

class CreateTokenIn(BaseModel):
    merchant_oid: str = Field(..., max_length=64)  # benzersiz sipariş no
    email: EmailStr
    user_name: str = Field(..., max_length=60)
    user_address: str = Field(..., max_length=400)
    user_phone: str = Field(..., max_length=20)
    payment_amount: float                          # örn 34.56 (TL)
    currency: str = "TL"                           # TL/TRY, USD, EUR, GBP, RUB
    basket: List[BasketItem]
    user_ip: Optional[str] = None                  # gönderilmezse sunucudan alınır
    no_installment: int = 0                        # 0|1
    max_installment: int = 0                       # 0,2..12
    timeout_limit: int = 30                        # dakika
    lang: str = "tr"                               # tr|en
    debug_on: int = 1                              # testte 1

    @validator("payment_amount")
    def _gt_zero(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("payment_amount must be > 0")
        return v

    @validator("currency")
    def _norm_currency(cls, v: str) -> str:
        return "TL" if v in ("TL", "TRY", "") else v

    @validator("no_installment")
    def _no_inst_ok(cls, v: int) -> int:
        if v not in (0, 1):
            raise ValueError("no_installment must be 0 or 1")
        return v

    @validator("max_installment")
    def _max_inst_ok(cls, v: int) -> int:
        if v not in (0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12):
            raise ValueError("max_installment must be 0 or 2..12")
        return v

    @validator("user_ip", pre=True, always=True)
    def _validate_ip(cls, v: Optional[str]) -> Optional[str]:
        if v:
            try:
                ipaddress.ip_address(v.split(",")[0].strip())
            except Exception:
                raise ValueError("user_ip invalid")
        return v

class CreateTokenOut(BaseModel):
    token: str
    iframe_url: str

# ======================== Helpers ===========================
def _require_env() -> None:
    if not (PAYTR_MERCHANT_ID and PAYTR_MERCHANT_KEY and PAYTR_MERCHANT_SALT):
        raise HTTPException(500, "PAYTR credentials missing in environment")

def _client_ip(req: Request, override: Optional[str]) -> str:
    if override:
        return override.split(",")[0].strip()
    for h in ("cf-connecting-ip", "x-real-ip", "x-forwarded-for"):
        v = req.headers.get(h)
        if v:
            return v.split(",")[0].strip()
    return (req.client.host or "").split(",")[0].strip()

def _basket_to_base64(basket: List[BasketItem]) -> str:
    # format: [['Ürün', '18.00', 1], ...] → base64(JSON)
    arr = [[it.name, f"{it.price:.2f}", it.quantity] for it in basket]
    js = json.dumps(arr, ensure_ascii=False, separators=(",", ":"))
    return base64.b64encode(js.encode("utf-8")).decode()

def _hmac_b64_sha256(key: str, msg: bytes) -> str:
    return base64.b64encode(hmac.new(key.encode(), msg, hashlib.sha256).digest()).decode()

def _s(d: Dict[str, Any], key: str) -> str:
    v = d.get(key)
    return "" if v is None else str(v)

# ================== 1) iFrame Token Oluştur =================
@router.post("/token", response_model=CreateTokenOut)
async def create_token(payload: CreateTokenIn, request: Request):
    """
    PayTR iFrame token oluşturur ve iFrame URL'ini döner.
    """
    _require_env()

    user_ip = _client_ip(request, payload.user_ip)
    if not user_ip:
        raise HTTPException(400, "user_ip required")

    # PayTR payment_amount kuruş cinsinden (int)
    payment_amount_int = int(round(payload.payment_amount * 100))
    if payment_amount_int <= 0:
        raise HTTPException(400, "payment_amount must be > 0")

    user_basket_b64 = _basket_to_base64(payload.basket)

    # hash_str = merchant_id + user_ip + merchant_oid + email + payment_amount + user_basket + no_installment + max_installment + currency + test_mode
    hash_str = (
        f"{PAYTR_MERCHANT_ID}{user_ip}{payload.merchant_oid}{payload.email}"
        f"{payment_amount_int}{user_basket_b64}{payload.no_installment}"
        f"{payload.max_installment}{payload.currency}{PAYTR_TEST_MODE}"
    ).encode()

    # DİKKAT: Token için SALT kullanılmaz (yalnızca callback doğrulamasında kullanılır)
    paytr_token_hmac = _hmac_b64_sha256(PAYTR_MERCHANT_KEY, hash_str + PAYTR_MERCHANT_SALT.encode())

    data = {
        "merchant_id": PAYTR_MERCHANT_ID,
        "user_ip": user_ip,
        "merchant_oid": payload.merchant_oid,
        "email": payload.email,
        "payment_amount": payment_amount_int,
        "paytr_token": paytr_token_hmac,
        "user_basket": user_basket_b64,
        "debug_on": payload.debug_on,
        "no_installment": payload.no_installment,
        "max_installment": payload.max_installment,
        "user_name": payload.user_name,
        "user_address": payload.user_address,
        "user_phone": payload.user_phone,
        "merchant_ok_url": PAYTR_OK_URL,
        "merchant_fail_url": PAYTR_FAIL_URL,
        "timeout_limit": payload.timeout_limit,
        "currency": payload.currency,
        "test_mode": PAYTR_TEST_MODE,
        "lang": payload.lang,
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(PAYTR_TOKEN_URL, data=data)

    try:
        res = r.json()
    except Exception:
        raise HTTPException(502, f"PAYTR response parse error: {r.text[:200]}")

    if res.get("status") != "success":
        raise HTTPException(400, f"PAYTR failed: {res.get('reason', 'unknown reason')}")

    token = res["token"]
    return CreateTokenOut(token=token, iframe_url=PAYTR_IFRAME_URL.format(token))

# ============ 2) Callback (Bildirim URL / IPN) =============
@router.post("/callback", response_class=PlainTextResponse)
async def paytr_callback(request: Request):
    """
    PAYTR ödeme sonucu bildirimi (server-side). Başarılı işlemde yalnızca 'OK' dön.
    Doğrulama: base64(hmac_sha256(merchant_oid + merchant_salt + status + total_amount, merchant_key))
    """
    _require_env()

    form = await request.form()
    post: Dict[str, Any] = dict(form)

    merchant_oid = _s(post, "merchant_oid")
    status_      = _s(post, "status")          # "success" | "failed"
    total_amount = _s(post, "total_amount")    # x100 (ör. 34.56 TL => "3456")
    given_hash   = _s(post, "hash")

    calc = base64.b64encode(
        hmac.new(
            PAYTR_MERCHANT_KEY.encode(),
            f"{merchant_oid}{PAYTR_MERCHANT_SALT}{status_}{total_amount}".encode(),
            hashlib.sha256,
        ).digest()
    ).decode()

    if calc != given_hash:
        # "OK" DÖNME! PayTR tekrar dener; sipariş sonuçlanmaz.
        return PlainTextResponse("BAD HASH", status_code=200)

    # ---------- İdempotent sipariş güncelle ----------
    order_ref = db.collection("orders").document(merchant_oid)
    snap = order_ref.get()
    if snap.exists:
        data = snap.to_dict() or {}
        if (data.get("payment", {}).get("provider") == "paytr" and
            data.get("payment", {}).get("status") in {"paid", "failed"}):
            # Zaten sonuçlanmış -> tekrar işlem yapmadan OK
            return PlainTextResponse("OK", status_code=200)

    # Opsiyonel: beklenen tutarla karşılaştırma (kuruş bazında)
    # expected_cents = int(round((snap.to_dict() or {}).get("total_amount", 0) * 100)) if snap.exists else None
    # if expected_cents is not None and expected_cents != int(total_amount or "0"):
    #     # Uyuşmazlık: yine de OK politikası bozulmasın; kayıt altına alın.
    #     pass

    paytr_info = {
        "merchant_oid": merchant_oid,
        "status": status_,
        "total_amount": (Decimal(total_amount) / Decimal(100)) if total_amount else Decimal("0"),
        "failed_reason_code": _s(post, "failed_reason_code"),
        "failed_reason_msg": _s(post, "failed_reason_msg"),
        "payment_type": _s(post, "payment_type"),
        "currency": _s(post, "currency"),
        "payment_amount_sent": (Decimal(_s(post, "payment_amount")) / Decimal(100))
                               if _s(post, "payment_amount") else None,
        "test_mode": _s(post, "test_mode") in {"1", "true", "True"},
        "raw": None,  # hassas veri saklamamak için kapattık; istersen post bırakabilirsin.
    }

    if status_ == "success":
        updates = {
            "status": "paid",
            "payment": {
                "provider": "paytr",
                "status": "paid",
                "received_total": paytr_info["total_amount"],
                "currency": paytr_info["currency"] or "TL",
                "payment_type": paytr_info["payment_type"] or "card",
                "reported_at": firestore.SERVER_TIMESTAMP,
                "paytr": paytr_info,
            },
            "updated_at": firestore.SERVER_TIMESTAMP,
        }
    else:
        updates = {
            "status": "payment_failed",
            "payment": {
                "provider": "paytr",
                "status": "failed",
                "reported_at": firestore.SERVER_TIMESTAMP,
                "paytr": paytr_info,
            },
            "updated_at": firestore.SERVER_TIMESTAMP,
        }

    order_ref.set(updates, merge=True)
    return PlainTextResponse("OK", status_code=200)
