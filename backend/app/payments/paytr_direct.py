# -*- coding: utf-8 -*-
from __future__ import annotations

import os, json, hmac, base64, hashlib, logging, re
from typing import List, Dict, Optional, Literal

import httpx
from fastapi import APIRouter, HTTPException, Request, Form, Query
from starlette.responses import PlainTextResponse, HTMLResponse
from pydantic import BaseModel, Field, EmailStr, validator

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

router = APIRouter(prefix="/paytr", tags=["paytr"])

log = logging.getLogger("paytr")
if not log.handlers:
    logging.basicConfig(level=logging.INFO)

# =========================
# HELPERS
# =========================
def to_cents(amount: float | str) -> str:
    """'349.90' -> '34990' (kuruş, noktasız string)"""
    s = str(amount).replace(",", ".")
    if "." in s:
        major, minor = (s.split(".") + ["0"])[:2]
        minor = (minor + "00")[:2]
        return f"{int(major)}{minor}"
    return s

def b64_str(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode("utf-8")

def hmac_b64(key_str: str, msg_str: str) -> str:
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

def _calc_direct_token(fields: Dict[str, str], mode: str) -> tuple[str, str, List[str]]:
    order = SIG_ORDER_MINIMAL.copy() if mode != "extended" else SIG_ORDER_EXTENDED.copy()
    if mode == "extended" and "card_type" in fields:
        idx = order.index("client_lang") + 1
        order.insert(idx, "card_type")
    pieces = [str(fields.get(k, "")) for k in order]
    s2s = "".join(pieces) + PAYTR_MERCHANT_SALT
    return hmac_b64(PAYTR_MERCHANT_KEY, s2s), s2s, order

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
    merchant_oid: str = Field(..., max_length=64, regex=r"^[A-Za-z0-9]+$")
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

    # Sepet -> Base64(JSON)
    basket_arr = [[i.name, f"{i.price:.2f}", i.quantity] for i in body.basket]
    user_basket_b64 = b64_str(json.dumps(basket_arr, ensure_ascii=False, separators=(",", ":")))

    amount_kurus = to_cents(body.payment_amount)

    fields: Dict[str, str] = {
        "merchant_id": PAYTR_MERCHANT_ID,
        "user_ip": ip,
        "merchant_oid": body.merchant_oid,
        "email": body.email,
        "payment_type": body.payment_type,
        "payment_amount": amount_kurus,
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

    if fields.get("debug_on") == "1":
        log.info("DIRECT sign_mode=%s mid=%s oid=%s ip=%s amt=%s test=%s non3d=%s",
                 PAYTR_SIGN_MODE, fields["merchant_id"], fields["merchant_oid"], fields["user_ip"],
                 fields["payment_amount"], fields["test_mode"], fields["non_3d"])
        log.info("DIRECT order=%s", " | ".join(order))
        log.info("DIRECT str2sign=%s", s2s)
        log.info("DIRECT token=%s", token)

    return DirectInitOut(action="https://www.paytr.com/odeme", fields=fields)

# =========================
# DIRECT — verify (token diff'i gör)
# =========================
@router.post("/direct/verify", response_model=VerifyOut)
async def paytr_direct_verify(payload: Dict[str, str]):
    f = {k: ("" if v is None else str(v)).strip() for k, v in payload.items()}
    notes = []
    if not f.get("payment_amount","").isdigit():
        notes.append("payment_amount kuruş string olmalı (15 TL -> '1500').")
    if not re.fullmatch(r"[A-Za-z0-9]+", f.get("merchant_oid","")):
        notes.append("merchant_oid sadece alfanümerik olmalı.")
    # user_basket kontrolü
    try:
        json.loads(base64.b64decode(f.get("user_basket","")).decode("utf-8"))
    except Exception:
        notes.append("user_basket Base64(JSON) çözümlenemedi.")

    calc, s2s, order = _calc_direct_token(f, PAYTR_SIGN_MODE)
    mid_match = (f.get("merchant_id") == PAYTR_MERCHANT_ID)

    return VerifyOut(
        match=(calc == f.get("paytr_token","")),
        mid_match=mid_match,
        calc_token=calc,
        posted_token=f.get("paytr_token",""),
        sign_mode=PAYTR_SIGN_MODE,
        string_to_sign=s2s,
        order_used=order,
        notes=notes or None
    )

# =========================
# iFRAME — init (taksiti kullanıcı seçer)
# =========================
@router.post("/iframe/init")
async def paytr_iframe_init(body: IframeInitIn, request: Request):
    ip = body.user_ip or client_ip(request, None)
    if not ip: raise HTTPException(400, "user_ip required")

    amount = str(int(round(body.payment_amount * 100)))
    basket = [[i.name, f"{i.price:.2f}", i.quantity] for i in body.basket]
    user_basket = b64_str(json.dumps(basket, ensure_ascii=False, separators=(",",":")))

    token, s2s = _calc_iframe_token(
        merchant_id=PAYTR_MERCHANT_ID,
        user_ip=ip,
        merchant_oid=body.merchant_oid,
        email=body.email,
        payment_amount=amount,
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
        "payment_amount": amount,
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
    total_amount: str = Form(...),    # örn "34990"
    hash: str         = Form(...),
):
    # expected = base64(hmac_sha256(key, merchant_oid + salt + status + total_amount))
    msg = f"{merchant_oid}{PAYTR_MERCHANT_SALT}{status}{total_amount}"
    expected = hmac_b64(PAYTR_MERCHANT_KEY, msg)

    if expected != hash:
        return PlainTextResponse("ERR", status_code=400)

    # TODO: merchant_oid ile idempotent sipariş güncelle
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

# (İsteğe bağlı demo formu tutmak istersen bırak; prod'da gerekli değil)
