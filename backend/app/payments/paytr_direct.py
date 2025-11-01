# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import json
import hmac
import base64
import hashlib
from typing import List, Dict, Optional, Literal
import re
import httpx
from fastapi import APIRouter, HTTPException, Request , Form , Query
from starlette.responses import PlainTextResponse, HTMLResponse
from pydantic import BaseModel, Field, EmailStr, validator

# -----------------------------------------------------------------------------
# ENV
# -----------------------------------------------------------------------------
PAYTR_MERCHANT_ID = os.getenv("PAYTR_MERCHANT_ID", "")
PAYTR_MERCHANT_KEY = os.getenv("PAYTR_MERCHANT_KEY", "")
PAYTR_MERCHANT_SALT = os.getenv("PAYTR_MERCHANT_SALT", "")
PAYTR_OK_URL = os.getenv("PAYTR_OK_URL", "https://example.com/payment/success")
PAYTR_FAIL_URL = os.getenv("PAYTR_FAIL_URL", "https://example.com/payment/fail")
PAYTR_TEST_MODE = "1" if os.getenv("PAYTR_TEST_MODE", "1").lower() in ("1", "true", "yes") else "0"

if not (PAYTR_MERCHANT_ID and PAYTR_MERCHANT_KEY and PAYTR_MERCHANT_SALT):
    raise RuntimeError("PAYTR env missing")

router = APIRouter(prefix="/paytr", tags=["paytr:direct"])

# -----------------------------------------------------------------------------
# HELPERS
# -----------------------------------------------------------------------------
def to_cents(amount: float | str) -> str:
    """'349.90' -> '34990' (noktasız/kuruş)"""
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

# -----------------------------------------------------------------------------
# SCHEMAS
# -----------------------------------------------------------------------------
class BasketItem(BaseModel):
    name: str = Field(..., max_length=100)
    price: float
    quantity: int = Field(..., ge=1)

class DirectInitIn(BaseModel):
    merchant_oid: str = Field(..., max_length=64)
    email: EmailStr
    payment_amount: float                    # TL cinsinden gelir; kuruşa çevrilecek
    payment_type: Literal["card", "card_points"] = "card"
    installment_count: int = Field(0, ge=0, le=12)
    currency: str = "TL"
    non_3d: Literal[0, 1] = 0
    client_lang: Literal["tr", "en"] = "tr"
    user_name: str = Field(..., max_length=60)
    user_address: str = Field(..., max_length=400)
    user_phone: str = Field(..., max_length=20)
    basket: List[BasketItem]
    card_type: Optional[
        Literal["advantage", "axess", "combo", "bonus", "cardfinans", "maximum", "paraf", "world", "saglamkart"]
    ] = None
    user_ip: Optional[str] = None
    debug_on: Literal[0, 1] = 1

    def _oid_alnum(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9]+", v):
            raise ValueError("merchant_oid must be alphanumeric")

        return v

class DirectInitOut(BaseModel):
    action: str
    fields: Dict[str, str]

# -----------------------------------------------------------------------------
# 1) Direct API — token üret ve PayTR alanlarını dön
# -----------------------------------------------------------------------------
@router.post("/direct/init", response_model=DirectInitOut)
async def paytr_direct_init(body: DirectInitIn, request: Request):
    """
    Direct API Step 1: PayTR'a POST edilecek alanları ve paytr_token'ı üretir.

    Notlar:
    - payment_amount kuruş (string) olmalı: 15 TL -> "1500"
    - user_basket Base64(JSON) olmalı
    - Token, KANONİK 10 alan + merchant_salt ile üretilir (sıra KRİTİK)
    """
    import re, logging
    log = logging.getLogger("paytr")
    if not log.handlers:
        logging.basicConfig(level=logging.INFO)

    ip = client_ip(request, body.user_ip)
    if not ip:
        raise HTTPException(400, "user_ip required")

    # OID sadece alfanümerik
    if not re.fullmatch(r"[A-Za-z0-9]+", body.merchant_oid or ""):
        raise HTTPException(status_code=400, detail="merchant_oid must be alphanumeric")

    # Sepeti -> [["Ürün","349.90",1], ...] -> JSON -> Base64
    basket_arr = [[i.name, f"{i.price:.2f}", i.quantity] for i in body.basket]
    user_basket_b64 = b64_str(json.dumps(basket_arr, ensure_ascii=False, separators=(",", ":")))

    # 15 -> "1500"
    amount_kurus = to_cents(body.payment_amount)

    # PayTR'a göndereceğin FIELDS (init cevabı)
    fields: Dict[str, str] = {
        "merchant_id": PAYTR_MERCHANT_ID,        # GERÇEK MID
        "user_ip": ip,
        "merchant_oid": body.merchant_oid,
        "email": body.email,
        "payment_type": body.payment_type,       # "card"
        "payment_amount": amount_kurus,          # örn: "1500"
        "currency": body.currency,               # "TL"
        "test_mode": PAYTR_TEST_MODE,            # "0" / "1" (string)
        "non_3d": str(body.non_3d),              # "0" / "1"
        "client_lang": body.client_lang,
        "merchant_ok_url": PAYTR_OK_URL,
        "merchant_fail_url": PAYTR_FAIL_URL,
        "user_name": body.user_name,
        "user_address": body.user_address,
        "user_phone": body.user_phone,
        "user_basket": user_basket_b64,         # Base64(JSON)
        "installment_count": str(body.installment_count),
        "debug_on": str(body.debug_on),
    }
    if body.card_type:
        fields["card_type"] = body.card_type  # token stringine dahil ETMİYORUZ

    # === TOKEN: SADECE KANONİK 10 ALAN + SALT ===
    # Sıra:
    # merchant_id, user_ip, merchant_oid, email, payment_amount,
    # payment_type, installment_count, currency, test_mode, non_3d, + SALT
    tok_str = (
        fields["merchant_id"] +
        fields["user_ip"] +
        fields["merchant_oid"] +
        fields["email"] +
        fields["payment_amount"] +
        fields["payment_type"] +
        fields["installment_count"] +
        fields["currency"] +
        fields["test_mode"] +
        fields["non_3d"] +
        PAYTR_MERCHANT_SALT
    )
    paytr_token = hmac_b64(PAYTR_MERCHANT_KEY, tok_str)
    fields["paytr_token"] = paytr_token

    # Debug log (sadece debug_on=1'de)
    if fields.get("debug_on") == "1":
        log.info("PAYTR mid=%s oid=%s ip=%s amt=%s test=%s non3d=%s",
                 fields["merchant_id"], fields["merchant_oid"], fields["user_ip"],
                 fields["payment_amount"], fields["test_mode"], fields["non_3d"])
        log.info("PAYTR str2sign=%s", tok_str)
        log.info("PAYTR token=%s", paytr_token)

    return DirectInitOut(action="https://www.paytr.com/odeme", fields=fields)


# -----------------------------------------------------------------------------
# 2) CALLBACK — PayTR bildirimini doğrula ve düz 'OK' dön
# -----------------------------------------------------------------------------
@router.post("/callback", response_class=PlainTextResponse, summary="PayTR Callback")
async def paytr_callback(
    merchant_oid: str = Form(...),
    status: str       = Form(...),    # "success" | "failed"
    total_amount: str = Form(...),    # örn "34990"
    hash: str         = Form(...),
):
    # Swagger'da bu 4 alan görünmeli
    print("CALLBACK HANDLER v2")  # doğru handler'a geldiğini logdan gör
    msg = f"{merchant_oid}{PAYTR_MERCHANT_SALT}{status}{total_amount}"
    expected = hmac_b64(PAYTR_MERCHANT_KEY, msg)

    if expected != hash:
        return PlainTextResponse("ERR", status_code=400)   # yanlış imza

    # TODO: siparişi güncelle (idempotent)
    return PlainTextResponse("OK", status_code=200)

# -----------------------------------------------------------------------------
# (Opsiyonel) Taksit Oranları — UI için özet
# -----------------------------------------------------------------------------
@router.get("/installments")
async def installments():
    """
    Taksit oranları (özet). Bazı hesaplarda ek token gerekebilir;
    kendi dokümanınıza göre parametreleri genişletin.
    """
    url = "https://www.paytr.com/odeme/taksit-oranlari"
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(url, data={"merchant_id": PAYTR_MERCHANT_ID})
        r.raise_for_status()
        return r.json()

# -----------------------------------------------------------------------------
# (Opsiyonel) Basit demo form (sadece yerel test için)
# -----------------------------------------------------------------------------
@router.get("/direct/demo-form", response_class=HTMLResponse)
async def demo_form():
    html = """
<!doctype html><meta charset="utf-8"><title>PayTR Direct Demo</title>
<body style="font-family:system-ui,Arial">
<h3>PayTR Direct API Demo</h3>
<p>/paytr/direct/init ile dönen alanları bu forma doldurup PayTR'a POST edin.</p>
<form method="post" action="https://www.paytr.com/odeme" id="f">
  <div><small>Bu form yalnızca test amaçlıdır.</small></div>
  <input type="hidden" name="merchant_id" id="merchant_id">
  <input type="hidden" name="user_ip" id="user_ip">
  <input type="hidden" name="merchant_oid" id="merchant_oid">
  <input type="hidden" name="email" id="email">
  <input type="hidden" name="payment_type" id="payment_type">
  <input type="hidden" name="payment_amount" id="payment_amount">
  <input type="hidden" name="currency" id="currency">
  <input type="hidden" name="test_mode" id="test_mode">
  <input type="hidden" name="non_3d" id="non_3d">
  <input type="hidden" name="client_lang" id="client_lang">
  <input type="hidden" name="merchant_ok_url" id="merchant_ok_url">
  <input type="hidden" name="merchant_fail_url" id="merchant_fail_url">
  <input type="hidden" name="user_name" id="user_name">
  <input type="hidden" name="user_address" id="user_address">
  <input type="hidden" name="user_phone" id="user_phone">
  <input type="hidden" name="user_basket" id="user_basket">
  <input type="hidden" name="installment_count" id="installment_count">
  <input type="hidden" name="paytr_token" id="paytr_token">
  <input type="hidden" name="debug_on" id="debug_on">
  <hr/>
  Kart İsmi: <input name="cc_owner" value="PAYTR TEST" required /><br/>
  Kart No: <input name="card_number" value="9792030394440796" required /><br/>
  SKT Ay: <input name="expiry_month" value="12" required /> SKT Yıl: <input name="expiry_year" value="30" required /><br/>
  CVV: <input name="cvv" value="000" required /><br/><br/>
  <button>PayTR'a Öde</button>
</form>
<script>
(async () => {
  const res = await fetch('/paytr/direct/init', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({
      merchant_oid: 'ORDER_TEST_001',
      email: 'customer@example.com',
      payment_amount: 349.90,
      payment_type: 'card',
      installment_count: 6,
      currency: 'TL',
      non_3d: 0,
      client_lang: 'tr',
      user_name: 'Ali Koyuncu',
      user_address: 'İstanbul, Türkiye',
      user_phone: '+905321234567',
      basket: [{name:'Ürün', price:349.90, quantity:1}],
      card_type: 'bonus',
      user_ip: null,
      debug_on: 1
    })
  });
  const data = await res.json();
  Object.entries(data.fields).forEach(([k,v])=>{
    const el = document.getElementById(k);
    if (el) el.value = v;
  });
})();
</script>
</body>"""
    return HTMLResponse(html)

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

def map_bank_to_card_type(bank_name: str | None) -> str | None:
    if not bank_name:
        return None
    name = bank_name.lower()
    if "garanti" in name: return "bonus"
    if "iş" in name or "is bank" in name: return "maximum"
    if "akbank" in name: return "axess"
    if "yapı kredi" in name or "yapi kredi" in name: return "world"
    if "halk" in name: return "paraf"
    if "finans" in name or "qnb" in name: return "cardfinans"
    if "ziraat" in name or "vakıf" in name: return "combo"
    if "kuveyt" in name: return "saglamkart"
    return None

@router.get("/bin-info")
async def get_card_info_bin_detail(bin_number: str = Query(..., min_length=6, max_length=8)):
    """
    PayTR /bin-detail uyumlu BIN sorgusu.
    - bin_number: ilk 6 veya 8 hane (tercihen 8 hane)
    - Dönen JSON içinde PayTR alanları ve mapped `card_type` yer alır.
    """
    # sanitize
    bin_number = bin_number.strip()
    if not bin_number.isdigit():
        raise HTTPException(status_code=400, detail="bin_number must be numeric")

    # --- build paytr_token according to doc: hash_str = bin_number + merchant_id + merchant_salt
    try:
        merchant_id = PAYTR_MERCHANT_ID  # from env in your module
        merchant_salt = PAYTR_MERCHANT_SALT
        merchant_key_str = PAYTR_MERCHANT_KEY
        if not (merchant_id and merchant_salt and merchant_key_str):
            raise RuntimeError("PAYTR env vars missing (merchant_id/key/salt)")

        # merchant_key must be bytes for HMAC; env gives string -> encode utf-8
        merchant_key_bytes = merchant_key_str.encode("utf-8")

        hash_str = f"{bin_number}{merchant_id}{merchant_salt}"
        digest = hmac.new(merchant_key_bytes, hash_str.encode("utf-8"), hashlib.sha256).digest()
        paytr_token = base64.b64encode(digest).decode("utf-8")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"token generation failed: {e}")

    # --- call PayTR bin-detail endpoint
    url = "https://www.paytr.com/odeme/api/bin-detail"
    payload = {
        "merchant_id": merchant_id,
        "bin_number": bin_number,
        "paytr_token": paytr_token
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # use form POST as examples in PayTR docs show params form-style
            r = await client.post(url, data=payload)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"failed to contact PayTR: {e}")

    # network / status handling
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"PayTR returned {r.status_code}")

    try:
        data = r.json()
    except Exception:
        raise HTTPException(status_code=502, detail="invalid json from PayTR")

    # PayTR response: check status
    status = data.get("status")
    if status == "error":
        err = data.get("err_msg", "unknown")
        raise HTTPException(status_code=502, detail=f"PayTR BIN detail error: {err}")
    if status == "failed":
        # BIN not known (e.g. foreign card) -> return that info
        return {"bin": bin_number, "status": "failed", "raw_response": data}

    # status == success -> extract fields
    # expected fields: cardType, businessCard, bank, brand, schema, bankCode, allow_non3d ...
    cardType = data.get("cardType")      # credit|debit
    businessCard = data.get("businessCard")
    bank = data.get("bank")
    brand = data.get("brand")            # axess, bonus,...
    schema = data.get("schema")
    bankCode = data.get("bankCode")
    allow_non3d = data.get("allow_non3d")

    mapped_card_type = map_bank_to_card_type(bank) or (brand if brand in {
        "advantage","axess","combo","bonus","cardfinans","maximum","paraf","world","saglamkart"
    } else None)

    return {
        "bin": bin_number,
        "status": "success",
        "cardType": cardType,
        "businessCard": businessCard,
        "bank": bank,
        "brand": brand,
        "schema": schema,
        "bankCode": bankCode,
        "allow_non3d": allow_non3d,
        "card_type_mapped": mapped_card_type,
        "raw_response": data
    }