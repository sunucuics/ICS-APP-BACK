# backend/app/routers/shipping_manual.py
from __future__ import annotations
from typing import Any, Dict, List, Optional, Literal
from datetime import datetime, timezone
from decimal import Decimal
import asyncio, inspect, os, requests
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from google.cloud.firestore_v1.field_path import FieldPath
from pydantic import BaseModel
from firebase_admin import firestore
from typing import Set, Iterable

from google.cloud.firestore_v1 import FieldFilter

from backend.app.config import db, settings
from backend.app.core.security import get_current_user, get_current_admin

# --- Mailer Import ---
from backend.app.core.mailer import (
    mailer_send,
    tpl_shipped_html,
    tpl_delivered_html,
    tpl_canceled_html,
)

import logging
log = logging.getLogger("orders")

# --- Routerlar ----------------------------------------------------------------
router = APIRouter(prefix="/orders", tags=["Orders"])
admin_router = APIRouter(prefix="/orders", tags=["Orders Admin"])

# --- Prefix & Koleksiyon Adları ----------------------------------------------
_PREFIX = (os.getenv("FIREBASE_COLLECTION_PREFIX") or "").strip()
def _prefixed(name: str) -> str:
    return f"{_PREFIX}{name}" if _PREFIX else name

# Merkezi Koleksiyon İsimleri
_CARTS = _prefixed("carts")
_ORDERS = _prefixed("orders")
_PAYMENT_SESSIONS = _prefixed("payment_sessions") 

# --- Yardımcı Fonksiyonlar ---------------------------------------------------

def _fetch_products_by_ids_from_db(product_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    ids = [pid for pid in set(product_ids) if pid]
    if not ids:
        return {}
    idx: Dict[str, Dict[str, Any]] = {}
    
    # 1. Toplu sorgu denemesi
    try:
        for i in range(0, len(ids), 10):
            batch = ids[i:i+10]
            q = (db.collection_group("items")
                 .where("id", "in", batch))
            for snap in q.stream():
                d = snap.to_dict() or {}
                d["id"] = snap.id
                if d.get("is_deleted"):
                    continue
                idx[snap.id] = d
                
    except Exception as e:
        # BUG FIX: Batch hatası olursa, 'batch' değişkeni tanımsız olabilir.
        # Bu yüzden hata durumunda orijinal 'ids' listesi üzerinden dönüyoruz.
        log.warning("DB fallback query failed (index?) — trying per-id equality: %s", e)
        
        for pid in ids:
            try:
                q1 = db.collection_group("items").where(FieldPath.document_id(), "==", pid).limit(1).stream()
                snap = next(q1, None)
                if snap:
                    d = snap.to_dict() or {}
                    d["id"] = snap.id
                    if not d.get("is_deleted"):
                        idx[snap.id] = d
            except Exception as e2:
                log.warning("Per-id fetch failed for %s: %s", pid, e2)
                
    return idx


def _product_index(product_ids: Set[str], request: Request) -> Dict[str, Dict[str, Any]]:
    api_products = _fetch_products_via_api(request)
    idx = _index_products_by_id(api_products)
    missing = [pid for pid in product_ids if pid not in idx]
    if missing:
        idx.update(_fetch_products_by_ids_from_db(missing))
    return idx

def _now():
    return datetime.now(timezone.utc)

def _merge_doc_id(doc_snap) -> Dict[str, Any]:
    data = doc_snap.to_dict() or {}
    data["id"] = doc_snap.id
    return data

def _get_product_via_api(request: Request, product_id: str) -> Optional[Dict[str, Any]]:
    base = str(request.base_url).rstrip("/")
    headers = {}
    auth = request.headers.get("authorization")
    if auth: headers["Authorization"] = auth
    for url in (f"{base}/products/{product_id}", f"{base}/products/?id={product_id}"):
        try:
            r = requests.get(url, headers=headers, timeout=5)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict) and str(data.get("id", "")) == str(product_id):
                    return data
                if isinstance(data, list):
                    for p in data:
                        if str(p.get("id","")) == str(product_id):
                            return p
        except Exception:
            pass
    return None

def _parse_money(v) -> float:
    try:
        s = str(v).strip().replace(",", ".")
        return float(s) if s else 0.0
    except Exception:
        return 0.0


def _unit_price_from_product(p: Optional[Dict[str, Any]]) -> float:
    if not isinstance(p, dict):
        return 0.0
    for k in ("final_price", "price"):
        v = _parse_money(p.get(k))
        if v > 0:
            return v
    return 0.0


def _get_product_from_db(product_id: str) -> Optional[Dict[str, Any]]:
    try:
        q = db.collection_group("items").where("id", "==", product_id)
        for snap in q.stream():
            d = snap.to_dict() or {}
            d["id"] = snap.id
            return d
    except Exception:
        pass
    return None

def _fetch_products_current(request: Request, ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    wanted = [pid for pid in set(ids) if pid]
    if not wanted:
        return out

    api_products = _fetch_products_via_api(request)
    idx = _index_products_by_id(api_products)
    for pid in wanted:
        if pid in idx:
            out[pid] = idx[pid]

    missing = [pid for pid in wanted if pid not in out]
    if missing:
        out.update(_fetch_products_by_ids_from_db(missing))
    return out


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
    # NOT: 'users' koleksiyonu genelde prefixlenmez, auth sistemi bozulmasın diye.
    usnap = db.collection("users").document(user_id).get()
    if usnap.exists:
        e = (usnap.to_dict() or {}).get("email") or ""
        return e.strip() or None
    return None

def _fetch_products_via_api(request: Request) -> List[Dict[str, Any]]:
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

def _load_cart_items(user_id: str, request: Request) -> List[Dict[str, Any]]:
    items_raw: List[Dict[str, Any]] = []
    cref = db.collection(_CARTS).document(user_id)

    csnap = cref.get()
    if csnap.exists:
        cdoc = csnap.to_dict() or {}
        items_raw = list(cdoc.get("items") or [])

    if not items_raw:
        try:
            for dsnap in cref.collection("items").stream():
                d = dsnap.to_dict() or {}
                items_raw.append(d)
        except Exception:
            pass

    if not items_raw:
        return []

    wanted_ids: set[str] = set()
    for it in items_raw:
        pid = str((it or {}).get("product_id") or (it or {}).get("id") or "").strip()
        if pid: wanted_ids.add(pid)

    catalog = _fetch_products_current(request, wanted_ids)

    out = []
    for it in items_raw:
        pid = str((it or {}).get("product_id") or (it or {}).get("id") or "").strip()
        qty = int((it or {}).get("qty") or (it or {}).get("quantity") or 0)
        if not pid or qty <= 0: continue
        p = catalog.get(pid)

        name = (
                (p or {}).get("title") or (p or {}).get("name") or (p or {}).get("label")
                or (it or {}).get("name") or (it or {}).get("title")
                or (p or {}).get("sku") or pid
        )

        unit = _unit_price_from_product(p)
        if unit <= 0:
            for k in ("final_price", "price"):
                unit = _parse_money((it or {}).get(k))
                if unit > 0:
                    break
        if unit <= 0:
            line_total = _parse_money((it or {}).get("line_total") or (it or {}).get("total"))
            if qty > 0 and line_total > 0:
                unit = line_total / float(qty)

        img = None
        if p and isinstance(p.get("images"), list) and p["images"]:
            img = p["images"][0]
        elif (it or {}).get("image_url"):
            img = it["image_url"]

        out.append({"product_id": pid, "name": name, "qty": qty, "price": unit, "final_price": unit, "image_url": img})
    return out


def _calc_totals(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    def _unit(it: Dict[str, Any]) -> Decimal:
        v = it.get("final_price", None)
        if v is None or str(v).strip() == "":
            v = it.get("price", 0)
        try:
            return Decimal(str(v).replace(",", "."))
        except Exception:
            return Decimal("0")

    subtotal = sum(Decimal(str(it.get("qty", 0) or 0)) * _unit(it) for it in items)
    grand = subtotal.quantize(Decimal("0.01"))
    cur = (getattr(settings, "currency", None) or "TRY").upper()
    return {"subtotal": float(grand), "grand_total": float(grand), "currency": cur}


def _build_customer(user_id: str) -> Dict[str, Any]:
    # NOT: 'users' koleksiyonu prefixlenmedi.
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
def _find_product_refs(items: List[Dict[str, Any]]) -> List[tuple]:
    """Her sipariş item'ı için (product_doc_ref, required_qty, product_data) döndürür."""
    stock_updates = []
    for item in items:
        pid = item.get("product_id")
        qty = item.get("qty", 0)
        if not pid or qty <= 0:
            continue
        docs = list(
            db.collection_group("items")
            .where(filter=FieldFilter("id", "==", pid))
            .limit(1)
            .stream()
        )
        if not docs:
            raise HTTPException(400, f"Ürün bulunamadı: {pid}")
        product_doc = docs[0]
        product_data = product_doc.to_dict() or {}
        current_stock = int(product_data.get("stock", 0))
        if current_stock < qty:
            product_name = product_data.get("title", pid)
            raise HTTPException(400, f"Yetersiz stok: {product_name} (Stok: {current_stock}, İstenen: {qty})")
        stock_updates.append((product_doc.reference, qty, product_data))
    return stock_updates


def _restore_stock(items: List[Dict[str, Any]]) -> None:
    """İptal edilen siparişin stoğunu geri yükler."""
    for item in items:
        pid = item.get("product_id")
        qty = item.get("qty", 0)
        if not pid or qty <= 0:
            continue
        try:
            docs = list(
                db.collection_group("items")
                .where(filter=FieldFilter("id", "==", pid))
                .limit(1)
                .stream()
            )
            if docs:
                ref = docs[0].reference
                current = int((docs[0].to_dict() or {}).get("stock", 0))
                ref.update({"stock": current + qty})
                log.info("STOCK_RESTORED pid=%s qty=%d new_stock=%d", pid, qty, current + qty)
        except Exception as e:
            log.warning("STOCK_RESTORE_FAILED pid=%s err=%s", pid, e)


@router.post("", summary="Sepetten sipariş oluştur (status=preparing)")
def create_order(request: Request, me: Dict = Depends(get_current_user)):
    user_id = me["id"]
    items = _load_cart_items(user_id, request)
    if not items:
        raise HTTPException(status_code=400, detail="Sepet boş.")

    # Stok kontrolü (transaction öncesi — ürün ref'lerini bul)
    stock_updates = _find_product_refs(items)

    totals = _calc_totals(items)
    now = _now()
    customer = _build_customer(user_id)

    order_ref = db.collection(_ORDERS).document()
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
        "payment": {
            "provider": "PAYTR",
            "status": "awaiting",
            "merchant_oid": order_ref.id},
    }

    # Atomik transaction: stok düş + sipariş oluştur
    @firestore.transactional
    def _create_order_txn(transaction):
        # 1) Stok kontrol (transaction read)
        for product_ref, required_qty, _ in stock_updates:
            snap = product_ref.get(transaction=transaction)
            data = snap.to_dict() or {}
            current_stock = int(data.get("stock", 0))
            if current_stock < required_qty:
                raise HTTPException(400, f"Yetersiz stok: {data.get('title', '')} (Stok: {current_stock}, İstenen: {required_qty})")

        # 2) Stok düş (transaction write)
        for product_ref, required_qty, _ in stock_updates:
            snap = product_ref.get(transaction=transaction)
            current = int((snap.to_dict() or {}).get("stock", 0))
            transaction.update(product_ref, {"stock": current - required_qty})

        # 3) Sipariş oluştur (transaction write)
        transaction.set(order_ref, payload)

    tx = db.transaction()
    _create_order_txn(tx)
    log.info("ORDER_CREATED oid=%s user=%s items=%d", order_ref.id, user_id, len(items))

    # Sepeti temizle (transaction dışında — sipariş oluştuysa temizle)
    try:
        db.collection(_CARTS).document(user_id).set({"items": []}, merge=True)
    except Exception:
        pass  # Sipariş oluştu, sepet temizleme başarısız olsa da devam et

    return {"id": order_ref.id, "message": "Siparişiniz alındı, kargonuz hazırlanıyor.", **payload}

# -----------------------------------------------------------------------------
# PUBLIC — Kullanıcının siparişlerini listele
# -----------------------------------------------------------------------------
@router.get("", summary="Kullanıcının siparişlerini listele (Aktif/Geçmiş)")
def list_my_orders(
    me: Dict = Depends(get_current_user),
    view_type: Literal["active", "past"] = Query("active", description="'active': Devam edenler, 'past': Tamamlananlar"),
    limit: int = Query(20, ge=1, le=100),
    start_after: Optional[str] = Query(None, description="ISO datetime (created_at) ile sayfalama"),
):
    user_id = me["id"]
    # _ORDERS (prefixed) kullanımı
    q = db.collection(_ORDERS).where("user_id", "==", user_id).where("is_deleted", "==", False)

    if view_type == "active":
        q = q.where("status", "in", ["preparing", "shipped"])
    else:
        q = q.where("status", "in", ["delivered", "canceled", "payment_failed"])

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
    
    for d in docs:
        data = d.to_dict() or {}
        data["id"] = d.id
        items.append(data)

    next_cursor = None
    if docs:
        last_ct = (docs[-1].to_dict() or {}).get("created_at")
        if isinstance(last_ct, datetime):
            next_cursor = last_ct.isoformat()

    return {"items": items, "next_cursor": next_cursor, "count": len(items)}


# -----------------------------------------------------------------------------
# PUBLIC — Sipariş detayı (FALLBACK EKLENDİ)
# -----------------------------------------------------------------------------
@router.get("/{order_id}", summary="Sipariş detay ve kargo durumu (kullanıcı)")
def get_order_public(order_id: str, me: Dict = Depends(get_current_user)):
    user_id = me["id"]
    
    # 1. Önce asıl ORDERS koleksiyonuna bak
    ref = db.collection(_ORDERS).document(order_id)
    snap = ref.get()
    
    if snap.exists:
        doc = snap.to_dict() or {}
        if doc.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="Bu sipariş size ait değil")
        if doc.get("is_deleted"):
            raise HTTPException(status_code=404, detail="Sipariş silinmiş")
        doc["id"] = order_id
        return doc

    # 2. FALLBACK: PAYMENT SESSION KONTROLÜ
    session_ref = db.collection(_PAYMENT_SESSIONS).document(order_id)
    session_snap = session_ref.get()

    if session_snap.exists:
        session = session_snap.to_dict() or {}
        if session.get("user_id") == user_id:
            # FIX: Frontend uyumluluğu için hem 'grand_total' hem 'grandTotal' gönderiyoruz.
            payable = session.get("payable_amount", 0)
            
            return {
                "id": order_id,
                "user_id": user_id,
                "status": "preparing", 
                "payment": {
                    "status": "processing",
                    "provider": "PAYTR"
                },
                "items": session.get("basket", []),
                "totals": {
                    "grand_total": payable,  # Yeni standard
                    "grandTotal": payable,   # PayTR/Frontend legacy uyumu
                    "currency": "TRY",
                    "subtotal": session.get("base_amount", 0)
                },
                "created_at": session.get("created_at", _now()),
                "is_temp_loading": True 
            }

    raise HTTPException(status_code=404, detail="Sipariş bulunamadı")

# -----------------------------------------------------------------------------
# PUBLIC — Ödeme bekleyen siparişi iptal et
# -----------------------------------------------------------------------------
@router.post("/{order_id}/cancel-awaiting", summary="Ödeme bekleyen siparişi iptal et")
def cancel_awaiting_order(order_id: str, me: Dict = Depends(get_current_user)):
    user_id = me["id"]
    ref = db.collection(_ORDERS).document(order_id)
    snap = ref.get()
    
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    
    doc = snap.to_dict() or {}
    if doc.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Bu sipariş size ait değil")
    if doc.get("is_deleted"):
        raise HTTPException(status_code=404, detail="Sipariş silinmiş")
    
    payment_status = (doc.get("payment") or {}).get("status", "").lower()
    if payment_status != "awaiting":
        raise HTTPException(
            status_code=400, 
            detail=f"Bu sipariş iptal edilemez. Ödeme durumu: {payment_status}"
        )
    
    now = _now()
    ref.update({
        "status": "canceled",
        "payment.status": "canceled",
        "updated_at": now,
        "status_history": firestore.ArrayUnion([
            {"status": "canceled", "at": now, "by": user_id, "meta": {"reason": "user_canceled_awaiting_payment"}}
        ])
    })
    
    return {"message": "Sipariş iptal edildi", "order_id": order_id}

# -----------------------------------------------------------------------------
# ADMIN — Kargoya verilecekler / ship / deliver / cancel / delete
# -----------------------------------------------------------------------------
@admin_router.get("/queue", summary="Kargoya verilecekler, kargoya verilenler ve teslim edilenler")
def list_ship_queue(request: Request, _: Dict = Depends(get_current_admin)):
    base = db.collection(_ORDERS).where("is_deleted", "==", False)

    preparing_q = (
        base.where("status", "==", "preparing")
        .order_by("created_at", direction=firestore.Query.ASCENDING)
        .stream()
    )
    shipped_q = (
        base.where("status", "==", "shipped")
        .order_by("created_at", direction=firestore.Query.ASCENDING)
        .stream()
    )
    delivered_q = (
        base.where("status", "==", "delivered")
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .stream()
    )

    preparing_raw = [_merge_doc_id(d) for d in preparing_q]
    shipped_raw   = [_merge_doc_id(d) for d in shipped_q]
    delivered_raw = [_merge_doc_id(d) for d in delivered_q]

    # Tüm siparişleri birleştir ve items'ları zenginleştir
    all_orders = preparing_raw + shipped_raw + delivered_raw
    
    all_product_ids = set()
    for order in all_orders:
        items = order.get("items") or []
        for item in items:
            pid = str(item.get("product_id") or item.get("id") or "").strip()
            if pid:
                all_product_ids.add(pid)
    
    product_index = _fetch_products_current(request, all_product_ids) if all_product_ids else {}
    
    def _enrich_order(order_doc: Dict[str, Any]) -> Dict[str, Any]:
        items = list(order_doc.get("items") or [])
        enriched_items = []
        for it in items:
            pid = str(it.get("product_id") or it.get("id") or "").strip()
            p = product_index.get(pid)
            
            name = (
                (p or {}).get("title") or (p or {}).get("name") or (p or {}).get("label")
                or it.get("name") or it.get("title")
                or (p or {}).get("sku") or pid
            )
            
            unit_price = _unit_price_from_product(p)
            if unit_price <= 0:
                for k in ("final_price", "price"):
                    unit_price = _parse_money(it.get(k))
                    if unit_price > 0:
                        break
            
            img = it.get("image_url")
            if not img and p and isinstance(p.get("images"), list) and p["images"]:
                img = p["images"][0]
            
            enriched_item = {
                **it,
                "name": name,
                "price": unit_price if unit_price > 0 else it.get("price", 0),
                "final_price": unit_price if unit_price > 0 else it.get("final_price", it.get("price", 0)),
                "image_url": img or it.get("image_url"),
            }
            enriched_items.append(enriched_item)
        
        return {**order_doc, "items": enriched_items}
    
    preparing = [_enrich_order(o) for o in preparing_raw]
    shipped = [_enrich_order(o) for o in shipped_raw]
    delivered = [_enrich_order(o) for o in delivered_raw]

    return {
        "preparing": preparing,
        "shipped": shipped,
        "delivered": delivered,
        "count": {
            "preparing": len(preparing),
            "shipped": len(shipped),
            "delivered": len(delivered),
        },
    }


class AdminShipRequest(BaseModel):
    tracking_number: str
    provider: str = "MANUAL"

class AdminCancelRequest(BaseModel):
    reason: Optional[str] = None

@admin_router.patch("/{order_id}/ship", summary="Siparişi 'shipped' yap ve e-posta gönder")
async def mark_shipped(order_id: str, body: AdminShipRequest, request: Request, admin: Dict = Depends(get_current_admin)):
    admin_id = admin["id"]

    def _do_firestore_work():
        ref = db.collection(_ORDERS).document(order_id)

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
        return _txn(tx), ref

    merged, ref = await asyncio.to_thread(_do_firestore_work)

    customer = merged.get("customer") or {}
    full_name = (customer.get("full_name") or "").strip()
    to_email = _customer_email_from_order(merged)

    if to_email:
        try:
            html = tpl_shipped_html(
                full_name,
                order_id,
                tracking_number=(merged.get("shipping") or {}).get("tracking_number", ""),
                tracking_url=(merged.get("shipping") or {}).get("tracking_url"),
            )
            await mailer_send(
                to=to_email,
                subject="Siparişiniz kargoya verildi",
                html=html,
                sender_name="ICS",
            )
            await asyncio.to_thread(ref.update, {"email_flags.shipped_sent": True})
        except Exception:
            log.exception("E-posta gönderilemedi | order_id=%s", order_id)

    merged["id"] = order_id
    return merged


@admin_router.patch("/{order_id}/deliver", summary="Siparişi 'delivered' yap ve e-posta gönder")
async def mark_delivered(order_id: str, request: Request, admin: Dict = Depends(get_current_admin)):
    admin_id = admin["id"]

    def _do_firestore_work():
        ref = db.collection(_ORDERS).document(order_id)

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
        return _txn(tx), ref

    merged, ref = await asyncio.to_thread(_do_firestore_work)

    customer = merged.get("customer") or {}
    full_name = (customer.get("full_name") or "").strip()
    to_email = _customer_email_from_order(merged)

    if to_email:
        try:
            html = tpl_delivered_html(full_name, order_id)
            await mailer_send(
                to=to_email,
                subject="Siparişiniz teslim edildi",
                html=html,
                sender_name="ICS",
            )
            await asyncio.to_thread(ref.update, {"email_flags.delivered_sent": True})
        except Exception:
            log.exception("E-posta gönderilemedi | order_id=%s", order_id)

    merged["id"] = order_id
    return merged


@admin_router.patch("/{order_id}/cancel", summary="Siparişi 'canceled' yap, stoğu geri yükle ve e-posta gönder")
async def cancel_order(order_id: str, body: AdminCancelRequest, request: Request, admin: Dict = Depends(get_current_admin)):
    admin_id = admin["id"]

    def _do_firestore_work():
        ref = db.collection(_ORDERS).document(order_id)

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
        result = _txn(tx)

        # Stok iadesi — transaction dışında (iptal kesinleştikten sonra)
        order_items = result.get("items", [])
        if order_items:
            _restore_stock(order_items)
            log.info("CANCEL_STOCK_RESTORED order=%s items=%d", order_id, len(order_items))

        return result, ref

    merged, ref = await asyncio.to_thread(_do_firestore_work)

    customer = merged.get("customer") or {}
    full_name = (customer.get("full_name") or "").strip()
    to_email = _customer_email_from_order(merged)

    if to_email:
        try:
            html = tpl_canceled_html(full_name, order_id, reason=body.reason)
            await mailer_send(
                to=to_email,
                subject="Siparişiniz iptal edildi",
                html=html,
                sender_name="ICS",
            )
            await asyncio.to_thread(ref.update, {"email_flags.canceled_sent": True})
        except Exception:
            log.exception("E-posta gönderilemedi | order_id=%s", order_id)

    merged["id"] = order_id
    return merged


@admin_router.delete("/{order_id}", summary="Siparişi soft delete yap (is_deleted=true)")
def delete_order(order_id: str, _: Dict = Depends(get_current_admin)):
    ref = db.collection(_ORDERS).document(order_id)
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    ref.update({"is_deleted": True, "updated_at": _now()})
    return {"ok": True}

@admin_router.post("/dev", summary="Test amaçlı örnek sipariş oluştur (sadece admin)")
def dev_create_order(_: Dict = Depends(get_current_admin)):
    """
    Test siparişi oluşturur. Fiyatlar TL cinsinden saklanır.
    """
    now = _now()
    ref = db.collection(_ORDERS).document()
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
        "items": [{"product_id": "p1", "name": "Lamba", "qty": 1, "price": 999.90, "final_price": 999.90}],
        "totals": {"subtotal": 999.90, "grand_total": 999.90, "currency": "TRY"},
        "note": None,
        "payment": {
            "provider": "PAYTR",
            "status": "awaiting",
            "merchant_oid": ref.id},
    }
    ref.set(payload)
    return {"id": ref.id, **payload}


@admin_router.post("/_email-test", summary="SMTP health check (admin)")
async def email_test(_: Dict = Depends(get_current_admin)):
    to = getattr(settings, "smtp_test_to", None) or getattr(settings, "smtp_from", None) or getattr(settings, "smtp_user", None)
    if not to:
        raise HTTPException(status_code=400, detail="smtp_test_to/smtp_from/smtp_user tanımlı değil")

    html = "<h3>ICS SMTP Test</h3><p>Bu bir deneme mesajıdır.</p>"
    try:
        await mailer_send(to=to, subject="ICS SMTP Test", html=html, sender_name="ICS")
        return {"ok": True, "to": to}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"SMTP error: {e.__class__.__name__}: {e}")


@admin_router.post("/_cleanup-awaiting", summary="Eski awaiting siparişleri iptal et (admin)")
def cleanup_awaiting_orders(
    minutes_old: int = Query(30, ge=5, le=1440, description="Kaç dakikadan eski siparişler iptal edilsin"),
    _: Dict = Depends(get_current_admin)
):
    from datetime import timedelta
    
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes_old)
    
    all_orders = db.collection(_ORDERS).where("is_deleted", "==", False).stream()
    
    canceled_count = 0
    canceled_ids = []
    now = _now()
    
    for doc in all_orders:
        data = doc.to_dict() or {}
        payment_status = (data.get("payment") or {}).get("status", "").lower()
        created_at = data.get("created_at")
        
        if payment_status != "awaiting":
            continue
            
        if created_at is None:
            continue
            
        if hasattr(created_at, 'timestamp'):
            created_dt = datetime.fromtimestamp(created_at.timestamp(), tz=timezone.utc)
        elif isinstance(created_at, datetime):
            created_dt = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
        else:
            continue
            
        if created_dt < cutoff:
            doc.reference.update({
                "status": "canceled",
                "payment.status": "expired",
                "updated_at": now,
                "status_history": firestore.ArrayUnion([
                    {"status": "canceled", "at": now, "by": "system", "meta": {"reason": "awaiting_payment_expired"}}
                ])
            })
            canceled_count += 1
            canceled_ids.append(doc.id)
    
    return {
        "message": f"{canceled_count} adet eski sipariş iptal edildi",
        "canceled_count": canceled_count,
        "canceled_ids": canceled_ids,
        "cutoff_minutes": minutes_old
    }