# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import json
import hmac
import base64
import hashlib
from typing import List, Dict, Optional, Literal

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

    @validator("currency")
    def _norm_currency(cls, v: str) -> str:
        return "TL" if v in ("", "TRY", "TL") else v

class DirectInitOut(BaseModel):
    action: str
    fields: Dict[str, str]

# -----------------------------------------------------------------------------
# 1) Direct API — token üret ve PayTR alanlarını dön
# -----------------------------------------------------------------------------
@router.post("/direct/init", response_model=DirectInitOut)
async def paytr_direct_init(body: DirectInitIn, request: Request):
    """
    Direct API Step 1: Kullanıcı formu doldurduktan sonra PayTR'ye POST edeceğin
    alanları ve imzayı (paytr_token) üretir.

    Önemli:
    - payment_amount kuruş (noktasız) string olmalı.
    - user_basket Base64-JSON olmalı.
    - Token hesaplamada alan sırası kritik (dokümandaki sırayla birleştirilir ve
      sonuna merchant_salt eklenerek merchant_key ile HMAC-SHA256 yapılır, Base64’e çevrilir).
      (Bkz. PayTR Direct/iFrame örnekleri)  # docs: token + salt + HMAC + Base64
    """
    ip = client_ip(request, body.user_ip)
    if not ip:
        raise HTTPException(400, "user_ip required")

    # Sepeti -> [["Ürün","349.90",1], ...] JSON -> Base64
    basket_arr = [[i.name, f"{i.price:.2f}", i.quantity] for i in body.basket]
    user_basket_b64 = b64_str(json.dumps(basket_arr, ensure_ascii=False, separators=(",", ":")))

    amount_kurus = to_cents(body.payment_amount)

    # === İMZA SIRASI ===
    # DİKKAT: PayTR dokümanındaki sıraya birebir uyulmalıdır. Aşağıdaki sıra Direct (card) için
    # yaygın kullanılan alanları içerir. Bankanız/PAYTR hesabınızın dokümanında farklılık varsa
    # burada aynı sıraya güncelleyin.
    #
    # Ref: "Data to be used in token production" ve örnek iFrame/Direct kodları (salt mesajın sonuna eklenir,
    # HMAC SHA256 merchant_key ile, çıktı Base64). :contentReference[oaicite:4]{index=4}
    sign_str = (
        f"{PAYTR_MERCHANT_ID}"
        f"{ip}"
        f"{body.merchant_oid}"
        f"{body.email}"
        f"{amount_kurus}"
        f"{body.payment_type}"
        f"{body.installment_count}"
        f"{body.currency}"
        f"{PAYTR_TEST_MODE}"
        f"{body.non_3d}"
        f"{PAYTR_MERCHANT_SALT}"
    )
    paytr_token = hmac_b64(PAYTR_MERCHANT_KEY, sign_str)

    fields: Dict[str, str] = {
        "merchant_id": PAYTR_MERCHANT_ID,
        "user_ip": ip,
        "merchant_oid": body.merchant_oid,
        "email": body.email,
        "payment_type": body.payment_type,
        "payment_amount": amount_kurus,           # <-- kuruş, noktasız
        "currency": body.currency,
        "test_mode": PAYTR_TEST_MODE,
        "non_3d": str(body.non_3d),
        "client_lang": body.client_lang,
        "merchant_ok_url": PAYTR_OK_URL,
        "merchant_fail_url": PAYTR_FAIL_URL,
        "user_name": body.user_name,
        "user_address": body.user_address,
        "user_phone": body.user_phone,
        "user_basket": user_basket_b64,          # <-- Base64 JSON
        "installment_count": str(body.installment_count),
        "paytr_token": paytr_token,
        "debug_on": str(body.debug_on),
    }
    if body.card_type:
        fields["card_type"] = body.card_type

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
async def get_card_info(bin_number: str = Query(..., min_length=6, max_length=6)):
    """
    Kart numarasının ilk 6 hanesinden (BIN) bankayı ve PayTR card_type değerini bulur.
    Örnek: /paytr/bin-info?bin_number=450712
    """
    try:
        url = f"https://www.paytr.com/odeme/api/installments?bin={bin_number}&amount=1000"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail="PayTR bağlantı hatası")
            data = resp.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"BIN sorgusu başarısız: {e}")

    # PayTR yanıtındaki banka adını bulmaya çalışalım
    bank_name = None
    if isinstance(data, dict):
        bank_name = data.get("bank_name") or data.get("bank") or data.get("bankname")

    card_type = map_bank_to_card_type(bank_name)
    return {
        "bin": bin_number,
        "bank_name": bank_name,
        "card_type": card_type,
        "raw_response": data
    }