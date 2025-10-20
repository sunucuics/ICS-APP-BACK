# backend/app/routers/shipping_manual.py
from __future__ import annotations
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from decimal import Decimal
import asyncio, inspect, os, requests

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from firebase_admin import firestore

from backend.app.config import db, settings
from backend.app.core.security import get_current_user, get_current_admin

# --- E-posta gönderimi (mevcut implementasyonu kullan; yoksa fallback) -------
_SEND_IMPL = None
try:
    from backend.app.core.security import send_email as _SEND_IMPL  # type: ignore
except Exception:
    try:
        from backend.app.core.email_utils import send_email as _SEND_IMPL  # type: ignore
    except Exception:
        _SEND_IMPL = None

from backend.app.core.mailer import (
    mailer_send,
    tpl_shipped_html,
    tpl_delivered_html,
    tpl_canceled_html,
)

import logging
log = logging.getLogger("orders")


async def _send_mail(*, to: str, subject: str, html: str, sender_name: str = "ICS") -> None:
    if _SEND_IMPL is not None:
        if inspect.iscoroutinefunction(_SEND_IMPL):
            await _SEND_IMPL(to=to, subject=subject, html=html, sender_name=sender_name)
            return
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: _SEND_IMPL(to=to, subject=subject, html=html, sender_name=sender_name))
        return
    # Fallback SMTP (konfigürasyon yoksa sessiz geç)
    import ssl, smtplib
    from email.message import EmailMessage
    from_addr = getattr(settings, "smtp_from", None) or getattr(settings, "smtp_user", None)
    host = getattr(settings, "smtp_host", None); port = getattr(settings, "smtp_port", None)
    user = getattr(settings, "smtp_user", None); password = getattr(settings, "smtp_password", None)
    use_starttls = bool(getattr(settings, "smtp_use_starttls", True))
    if not (host and port and user and password and from_addr):
        return
    msg = EmailMessage()
    msg["To"] = to; msg["From"] = f"{sender_name} <{from_addr}>"; msg["Subject"] = subject
    msg.set_content("HTML içeriği göremiyorsanız e-postayı HTML olarak görüntüleyin.")
    msg.add_alternative(html, subtype="html")
    def _send_blocking():
        context = ssl.create_default_context()
        if use_starttls:
            with smtplib.SMTP(host, port) as s:
                s.ehlo(); s.starttls(context=context); s.ehlo(); s.login(user, password); s.send_message(msg)
        else:
            with smtplib.SMTP_SSL(host, port, context=context) as s:
                s.login(user, password); s.send_message(msg)
    loop = asyncio.get_event_loop(); await loop.run_in_executor(None, _send_blocking)

# --- Routerlar ----------------------------------------------------------------
router = APIRouter(prefix="/orders", tags=["Orders"])
admin_router = APIRouter(prefix="/orders", tags=["Orders Admin"])

# --- Prefix & koleksiyon adları ----------------------------------------------
_PREFIX = (os.getenv("FIREBASE_COLLECTION_PREFIX") or "").strip()
def _prefixed(name: str) -> str:
    return f"{_PREFIX}{name}" if _PREFIX else name
_CARTS = _prefixed("carts")

# --- Yardımcılar --------------------------------------------------------------
def _now():
    return datetime.now(timezone.utc)

def _merge_doc_id(doc_snap) -> Dict[str, Any]:
    data = doc_snap.to_dict() or {}
    data["id"] = doc_snap.id
    return data

def _ensure_transition(current: str, target: str):
    valid = {
        "preparing": {"shipped", "canceled"},
        "shipped": {"delivered", "canceled"},
        "delivered": set(),
        "canceled": set(),
    }
    cur = (current or "preparing").lower()
    if target not in valid.get(cur, set()):
        raise HTTPException(status_code=409, detail=f"Geçersiz durum geçişi: '{current}' -> '{target}'")

def _customer_email_from_order(order_doc: Dict[str, Any]) -> Optional[str]:
    customer = order_doc.get("customer") or {}
    email = (customer.get("email") or "").strip()
    if email:
        return email
    user_id = order_doc.get("user_id")
    if not user_id:
        return None
    usnap = db.collection("users").document(user_id).get()
    if usnap.exists:
        e = (usnap.to_dict() or {}).get("email") or ""
        return e.strip() or None
    return None

def _render_mail_shipped(full_name: str, order_id: str, tracking_number: str) -> str:
    base = getattr(settings, "frontend_base_url", "") or ""
    link = f"{base}/orders/{order_id}" if base else "#"
    return (
        f"<h2>Kargonuz yola çıktı</h2>"
        f"<p>Merhaba {full_name or ''}, siparişiniz kargoya verilmiştir.</p>"
        f"<p>Takip numaranız: <b>{tracking_number}</b></p>"
        f"<p>Detaylar: <a href='{link}'>{link}</a></p>"
    )

def _render_mail_delivered(full_name: str, order_id: str) -> str:
    base = getattr(settings, "frontend_base_url", "") or ""
    link = f"{base}/orders/{order_id}" if base else "#"
    return (
        f"<h2>Teslim edildi</h2>"
        f"<p>Merhaba {full_name or ''}, siparişiniz başarıyla teslim edilmiştir.</p>"
        f"<p>Detaylar: <a href='{link}'>{link}</a></p>"
    )

def _render_mail_canceled(full_name: str, order_id: str) -> str:
    base = getattr(settings, "frontend_base_url", "") or ""
    link = f"{base}/orders/{order_id}" if base else "#"
    return (
        f"<h2>Siparişiniz iptal edildi</h2>"
        f"<p>Merhaba {full_name or ''}, siparişiniz iptal edilmiştir.</p>"
        f"<p>Detaylar: <a href='{link}'>{link}</a></p>"
    )

# --- /products API'sini kullanarak katalog çekme (carts.py ile aynı yaklaşım) -
def _fetch_products_via_api(request: Request) -> List[Dict[str, Any]]:
    """
    /products/ endpoint'ini çağırır; aynı ID ve alanları kullanırız.
    """
    base = str(request.base_url).rstrip("/")
    url = f"{base}/products/"
    headers: Dict[str, str] = {}
    auth = request.headers.get("authorization")
    if auth:
        headers["Authorization"] = auth
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data["items"]
    except Exception:
        pass
    return []

def _index_products_by_id(products: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    idx: Dict[str, Dict[str, Any]] = {}
    for p in products:
        pid = str(p.get("id", "")).strip()
        if pid:
            idx[pid] = p
    return idx

def _money_from_product(p: Optional[Dict[str, Any]]) -> Decimal:
    if not p:
        return Decimal("0")
    if p.get("final_price") is not None:
        return Decimal(str(p.get("final_price", 0)))
    return Decimal(str(p.get("price", 0)))

# --- Sepeti oku ve /products ile zenginleştir --------------------------------
def _load_cart_items(user_id: str, request: Request) -> List[Dict[str, Any]]:
    """
    carts/{uid} dokümanındaki {product_id, qty} satırlarını alır,
    /products ile eşleştirip {name, price, image_url} doldurur.
    Subcollection (carts/{uid}/items) desteği de vardır.
    """
    items_raw: List[Dict[str, Any]] = []
    cref = db.collection(_CARTS).document(user_id)

    # 1) doc.items[]
    csnap = cref.get()
    if csnap.exists:
        cdoc = csnap.to_dict() or {}
        items_raw = list(cdoc.get("items") or [])

    # 2) alt koleksiyon
    if not items_raw:
        try:
            for dsnap in cref.collection("items").stream():
                d = dsnap.to_dict() or {}
                items_raw.append(d)
        except Exception:
            pass

    # Sepet gerçekten boşsa direkt dönelim
    if not items_raw:
        return []

    # Katalogu /products'tan çek ve id->product index'i kur
    products = _fetch_products_via_api(request)
    catalog = _index_products_by_id(products)

    out: List[Dict[str, Any]] = []
    for it in items_raw:
        pid = str((it or {}).get("product_id") or (it or {}).get("id") or "").strip()
        qty = int((it or {}).get("qty") or (it or {}).get("quantity") or 0)
        if not pid or qty <= 0:
            continue

        p = catalog.get(pid)
        name = (p or {}).get("title") or (p or {}).get("name") or (it or {}).get("name") or ""
        price = float(_money_from_product(p)) if p else float((it or {}).get("price") or 0)
        img = None
        if p and isinstance(p.get("images"), list) and p["images"]:
            img = p["images"][0]
        elif it.get("image_url"):
            img = it["image_url"]

        out.append({"product_id": pid, "name": name, "qty": qty, "price": price, "image_url": img})

    return out

def _calc_totals(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    subtotal = sum((i.get("qty", 0) or 0) * float(i.get("price", 0) or 0) for i in items)
    cur = (getattr(settings, "currency", None) or "TRY").upper()
    return {"subtotal": round(subtotal, 2), "grand_total": round(subtotal, 2), "currency": cur}


def _build_customer(user_id: str) -> Dict[str, Any]:
    usnap = db.collection("users").document(user_id).get()
    u = usnap.to_dict() or {} if usnap.exists else {}
    addrs = (u.get("addresses") or [])
    addr0 = addrs[0] if isinstance(addrs, list) and addrs else {}
    return {
        "full_name": u.get("name") or u.get("full_name") or "",
        "email": u.get("email") or "",
        "phone": u.get("phone") or u.get("phone_number") or "",
        "address": addr0,
    }

# -----------------------------------------------------------------------------
# PUBLIC — (BODY YOK) Sepetten sipariş oluştur
# -----------------------------------------------------------------------------
@router.post("", summary="Sepetten sipariş oluştur (status=preparing)")
async def create_order(request: Request, me: Dict = Depends(get_current_user)):
    user_id = me["id"]
    items = _load_cart_items(user_id, request)
    if not items:
        raise HTTPException(status_code=400, detail="Sepet boş.")

    totals = _calc_totals(items)
    now = _now()
    customer = _build_customer(user_id)

    ref = db.collection("orders").document()
    payload: Dict[str, Any] = {
        "user_id": user_id,
        "created_at": now,
        "updated_at": now,
        "is_deleted": False,
        "status": "preparing",
        "status_history": [{"status": "preparing", "at": now, "by": user_id}],
        "customer": customer,
        "shipping": {"provider": "MANUAL"},
        "items": items,
        "totals": totals,
        "note": None,
        "payment": {},
    }
    ref.set(payload)

    # Sepeti temizle
    try:
        db.collection(_CARTS).document(user_id).set({"items": []}, merge=True)
        for dsnap in db.collection(_CARTS).document(user_id).collection("items").stream():
            dsnap.reference.delete()
    except Exception:
        pass

    return {"id": ref.id, "message": "Siparişiniz alındı, kargonuz hazırlanıyor.", **payload}

# -----------------------------------------------------------------------------
# PUBLIC — Kullanıcının siparişlerini listele
# -----------------------------------------------------------------------------
@router.get("", summary="Kullanıcının siparişlerini listele (sayfalama destekli)")
async def list_my_orders(
    me: Dict = Depends(get_current_user),
    status_filter: Optional[str] = Query(None, alias="status", pattern="^(preparing|shipped|delivered|canceled)$"),
    limit: int = Query(20, ge=1, le=100),
    start_after: Optional[str] = Query(None, description="ISO datetime (created_at) ile sayfalama")
):
    user_id = me["id"]
    q = db.collection("orders").where("user_id", "==", user_id).where("is_deleted", "==", False)
    if status_filter:
        q = q.where("status", "==", status_filter)
    q = q.order_by("created_at", direction=firestore.Query.DESCENDING)

    cursor_dt = None
    if start_after:
        try:
            cursor_dt = datetime.fromisoformat(start_after.replace("Z", "+00:00"))
        except Exception:
            raise HTTPException(status_code=400, detail="start_after ISO formatında olmalı")
    if cursor_dt:
        q = q.start_after(cursor_dt)

    docs = list(q.limit(limit).stream())
    items = []
    next_cursor = None
    for d in docs:
        data = d.to_dict() or {}
        data["id"] = d.id
        items.append(data)
    if docs:
        last_ct = (docs[-1].to_dict() or {}).get("created_at")
        if isinstance(last_ct, datetime):
            next_cursor = last_ct.isoformat()
    return {"items": items, "next_cursor": next_cursor, "count": len(items)}

# -----------------------------------------------------------------------------
# PUBLIC — Sipariş detayı
# -----------------------------------------------------------------------------
@router.get("/{order_id}", summary="Sipariş detay ve kargo durumu (kullanıcı)")
async def get_order_public(order_id: str, me: Dict = Depends(get_current_user)):
    user_id = me["id"]
    ref = db.collection("orders").document(order_id)
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    doc = snap.to_dict() or {}
    if doc.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Bu sipariş size ait değil")
    if doc.get("is_deleted"):
        raise HTTPException(status_code=404, detail="Sipariş silinmiş")
    doc["id"] = order_id
    return doc

# -----------------------------------------------------------------------------
# ADMIN — Kargoya verilecekler / ship / deliver / cancel / delete
# -----------------------------------------------------------------------------
@admin_router.get("/queue", summary="Kargoya verilecek siparişler (status=preparing)")
async def list_ship_queue(_: Dict = Depends(get_current_admin)):
    q = (
        db.collection("orders")
        .where("status", "==", "preparing")
        .where("is_deleted", "==", False)
        .order_by("created_at", direction=firestore.Query.ASCENDING)
        .stream()
    )
    return [_merge_doc_id(d) for d in q]

class AdminShipRequest(BaseModel):
    tracking_number: str
    provider: str = "MANUAL"

class AdminCancelRequest(BaseModel):
    reason: Optional[str] = None

@admin_router.patch("/{order_id}/ship", summary="Siparişi 'shipped' yap ve e-posta gönder")
async def mark_shipped(order_id: str, body: AdminShipRequest, admin: Dict = Depends(get_current_admin)):
    admin_id = admin["id"]
    ref = db.collection("orders").document(order_id)

    @firestore.transactional
    def _txn(tx):
        snap = ref.get(transaction=tx)
        if not snap.exists:
            raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
        doc = snap.to_dict() or {}
        _ensure_transition(doc.get("status") or "preparing", "shipped")
        if not body.tracking_number:
            raise HTTPException(status_code=400, detail="Takip numarası zorunlu")
        now = _now()
        update = {
            "status": "shipped",
            "shipping": {
                "provider": body.provider,
                "tracking_number": body.tracking_number,
                "shipped_at": now,
                "delivered_at": (doc.get("shipping") or {}).get("delivered_at"),
            },
            "updated_at": now,
            "status_history": firestore.ArrayUnion([
                {"status": "shipped", "at": now, "by": admin_id, "meta": {"tracking_number": body.tracking_number}}
            ]),
        }
        tx.update(ref, update)
        return {**doc, **update}

    tx = db.transaction()
    merged = _txn(tx)

    # Mail
    customer = merged.get("customer") or {}
    full_name = (customer.get("full_name") or "").strip()
    to_email = _customer_email_from_order(merged)
    if to_email:
        try:
            html = tpl_shipped_html(
                full_name,
                order_id,
                items=merged.get("items") or [],
                totals=merged.get("totals") or {},
                address=(customer.get("address") if isinstance(customer.get("address"), dict) else None),
                tracking_number=(merged.get("shipping") or {}).get("tracking_number", ""),
                tracking_url=(merged.get("shipping") or {}).get("tracking_url"),  # varsa ekle
            )
            await mailer_send(
                to=to_email,
                subject=f"#{order_id} siparişiniz kargoya verildi",
                html=html,
                sender_name="ICS",
            )
            ref.update({"email_flags.shipped_sent": True})
        except Exception as e:
            log.exception("E-posta gönderilemedi | order_id=%s", order_id)

    merged["id"] = order_id
    return merged

@admin_router.patch("/{order_id}/deliver", summary="Siparişi 'delivered' yap ve e-posta gönder")
async def mark_delivered(order_id: str, admin: Dict = Depends(get_current_admin)):
    admin_id = admin["id"]
    ref = db.collection("orders").document(order_id)

    @firestore.transactional
    def _txn(tx):
        snap = ref.get(transaction=tx)
        if not snap.exists:
            raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
        doc = snap.to_dict() or {}
        _ensure_transition(doc.get("status") or "preparing", "delivered")
        now = _now()
        update = {
            "status": "delivered",
            "shipping": {**(doc.get("shipping") or {}), "delivered_at": now},
            "updated_at": now,
            "status_history": firestore.ArrayUnion([
                {"status": "delivered", "at": now, "by": admin_id, "meta": {}}
            ]),
        }
        tx.update(ref, update)
        return {**doc, **update}

    tx = db.transaction()
    merged = _txn(tx)

    # Mail
    customer = merged.get("customer") or {}
    full_name = (customer.get("full_name") or "").strip()
    to_email = _customer_email_from_order(merged)
    if to_email:
        try:
            html = tpl_delivered_html(
                full_name,
                order_id,
                items=merged.get("items") or [],
                totals=merged.get("totals") or {},
                address=(customer.get("address") if isinstance(customer.get("address"), dict) else None),
            )
            await mailer_send(
                to=to_email,
                subject=f"#{order_id} siparişiniz teslim edildi",
                html=html,
                sender_name="ICS",
            )
            ref.update({"email_flags.delivered_sent": True})
        except Exception as e:
            log.exception("E-posta gönderilemedi | order_id=%s", order_id)

    merged["id"] = order_id
    return merged

@admin_router.patch("/{order_id}/cancel", summary="Siparişi 'canceled' yap ve e-posta gönder")
async def cancel_order(order_id: str, body: AdminCancelRequest, admin: Dict = Depends(get_current_admin)):
    admin_id = admin["id"]
    ref = db.collection("orders").document(order_id)

    @firestore.transactional
    def _txn(tx):
        snap = ref.get(transaction=tx)
        if not snap.exists:
            raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
        doc = snap.to_dict() or {}
        _ensure_transition(doc.get("status") or "preparing", "canceled")
        now = _now()
        update = {
            "status": "canceled",
            "updated_at": now,
            "status_history": firestore.ArrayUnion([
                {"status": "canceled", "at": now, "by": admin_id, "meta": {"reason": body.reason}}
            ]),
        }
        tx.update(ref, update)
        return {**doc, **update}

    tx = db.transaction()
    merged = _txn(tx)

    # Mail
    customer = merged.get("customer") or {}
    full_name = (customer.get("full_name") or "").strip()
    to_email = _customer_email_from_order(merged)
    if to_email:
        try:
            html = tpl_canceled_html(
                full_name,
                order_id,
                reason=body.reason,
                items=merged.get("items") or [],
                totals=merged.get("totals") or {},
                address=(customer.get("address") if isinstance(customer.get("address"), dict) else None),
            )
            await mailer_send(
                to=to_email,
                subject=f"#{order_id} siparişiniz iptal edildi",
                html=html,
                sender_name="ICS",
            )
            ref.update({"email_flags.canceled_sent": True})
        except Exception as e:
            log.exception("E-posta gönderilemedi | order_id=%s", order_id)

    merged["id"] = order_id
    return merged

@admin_router.delete("/{order_id}", summary="Siparişi soft delete yap (is_deleted=true)")
async def delete_order(order_id: str, _: Dict = Depends(get_current_admin)):
    ref = db.collection("orders").document(order_id)
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    ref.update({"is_deleted": True, "updated_at": _now()})
    return {"ok": True}

@admin_router.post("/dev", summary="Test amaçlı örnek sipariş oluştur (sadece admin)")
async def dev_create_order(_: Dict = Depends(get_current_admin)):
    now = _now()
    ref = db.collection("orders").document()
    payload = {
        "user_id": "demo-user",
        "created_at": now,
        "updated_at": now,
        "is_deleted": False,
        "status": "preparing",
        "status_history": [{"status": "preparing", "at": now, "by": "demo-user"}],
        "customer": {
            "full_name": "Demo Kullanıcı",
            "email": "demo@example.com",
            "phone": "+90 555 000 0000",
            "address": {"line1": "Örnek Mah., Örnek Sk. No:1", "city": "İstanbul", "postal_code": "34000", "country": "TR"},
        },
        "shipping": {"provider": "MANUAL"},
        "items": [{"product_id": "p1", "name": "Lamba", "qty": 1, "price": 999.90}],
        "totals": {"grand_total": 999.90, "currency": "TRY"},
        "note": None,
        "payment": {},
    }
    ref.set(payload)
    return {"id": ref.id, **payload}
