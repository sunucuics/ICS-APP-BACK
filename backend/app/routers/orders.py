from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from firebase_admin import firestore
from google.cloud.firestore_v1 import SERVER_TIMESTAMP
from typing import Any, Dict, List, Optional
import uuid
from app.routers import users as users_router
from fastapi.responses import JSONResponse
from google.api_core.exceptions import FailedPrecondition
from app.config import db, settings
from app.schemas.order import OrderCreate, OrderOut, OrderItem , PickupBody
from app.integrations.shipping_provider import (
    create_shipment_with_setorder,
    get_status_with_integration_code,
)
from app.core.security import get_current_user as get_principal
from app.core.security import get_current_admin
from app.services.orders_helpers import (
    _fetch_active_address,
    _call_users_current_address,
    _resolve_active_address,
    _extract_uid,
    _extract_name,
    _extract_phone,
    _to_order_item,
    _fetch_cart_items,
    _clear_cart,
    _order_doc_to_out,
    _doc_to_out,
    _map_aras_status,
    coerce_item,
    calc_totals,
    build_order_doc,
    order_doc_to_out,
    ensure_aras_env_or_raise,
    aras_single_package,
    fetch_cart_items,
    clear_cart,
    resolve_active_address,
    extract_uid,
    extract_name,
    extract_phone,
    enrich_items_from_products,
)
from app.services.fulfillment import auto_after_create , attach_label, schedule_pickup
from datetime import date
from google.cloud.firestore_v1.base_query import FieldFilter
import inspect


router = APIRouter(prefix="/orders", tags=["Orders"])
admin_router = APIRouter(prefix="/orders", tags=["Admin Orders"])



def _create_order_impl(
    payload: OrderCreate,
    simulate: bool = Query(False, description="True ise Aras'a istek atılmaz, sahte takip no üretilir."),
    clear_cart_on_success: bool = Query(True, description="Sipariş başarılıysa sepeti temizle."),
    checkout_id: Optional[str] = Query(
        None,
        description="Aynı checkout için tek sipariş üretmek üzere idempotent anahtar (ör. UUID).",
    ),
    principal=Depends(get_principal),
):
    """
    TEK CHECKOUT → TEK SİPARİŞ (TEK TAKİP NO)
    - Sepetin tamamı 'items' altında saklanır.
    - Aras entegrasyonunda tek paket/tek takip numarası oluşturulur.
    - Aynı checkout_id ile tekrar çağrılırsa, yeni sipariş açmaz.
    """
    uid = extract_uid(principal)
    if not uid:
        raise HTTPException(status_code=401, detail="Oturum bulunamadı.")

    # 0) Aktif adres
    try:
        addr = resolve_active_address(principal)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Aktif adres çözümleme hatası: {e}")
    if not addr:
        raise HTTPException(status_code=400, detail="Aktif adres bulunamadı. Lütfen bir adres seçin.")

    # 1) Item'lar — CART-FIRST
    cart_items_raw = fetch_cart_items(uid)
    request_items_raw = payload.items or []
    raw_items = cart_items_raw or request_items_raw
    if not raw_items:
        raise HTTPException(status_code=400, detail="Sepet boş. Lütfen önce ürün ekleyin.")

    items = [coerce_item(it) for it in raw_items]
    items = enrich_items_from_products(items)
    currency = (items[0]["currency"] if items else "TRY").upper()
    totals = calc_totals(items, currency=currency)

    # 2) Idempotent kontrol (checkout_id varsa)
    if checkout_id:
        try:
            existing = list(
                db.collection("orders")
                  .where(filter=FieldFilter("user_id", "==", uid))
                  .where(filter=FieldFilter("_checkout_id", "==", checkout_id))
                  .limit(1)
                  .stream()
            )
            if existing:
                return order_doc_to_out(existing[0])
        except Exception:
            # Lookup başarısızsa devam et
            pass

    # 3) Sipariş ID
    order_id = str(uuid.uuid4())

    # 4) Kargo (tek paket/tek takip)
    if simulate:
        tracking_no = f"FAKE-{order_id[:8]}"
        log_msg = "Simülasyon: Aras'a istek gönderilmedi."
    else:
        try:
            ensure_aras_env_or_raise()
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        receiver = {
            "name": addr.get("name") or addr.get("label") or (extract_name(principal) or "Müşteri"),
            "phone": extract_phone(principal),
            "address": addr,
        }
        ok, tracking_no, log_msg = aras_single_package(
            receiver=receiver,
            integration_code=order_id,
            items=items,
        )
        if not ok:
            raise HTTPException(status_code=502, detail=f"Kargo oluşturulamadı: {log_msg}")

    # 5) Firestore yazımı
    order_doc = build_order_doc(
        uid=uid,
        order_id=order_id,
        addr=addr,
        items=items,
        totals=totals,
        tracking_no=tracking_no,
        note=payload.note,
        simulated=simulate,
        checkout_id=checkout_id,
        log_msg=log_msg,
    )
    db.collection("orders").document(order_id).set(order_doc)

    # 6) Sepeti temizle (opsiyonel)
    if clear_cart_on_success and cart_items_raw:
        clear_cart(uid)

    # 7) Opsiyonel otomasyonlar
    try:
        auto_after_create(order_id=order_id, integration_code=order_id)
    except Exception:
        pass

    saved = db.collection("orders").document(order_id).get()
    return order_doc_to_out(saved)


@router.post("", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
def create_order_no_slash(
    payload: OrderCreate,
    simulate: bool = Query(False, description="True ise Aras'a istek atılmaz, sahte takip no üretilir."),
    clear_cart_on_success: bool = Query(True, description="Sipariş başarılıysa sepeti temizle."),
    checkout_id: Optional[str] = Query(
        None,
        description="Aynı checkout için tek sipariş üretmek üzere idempotent anahtar (ör. UUID).",
    ),
    principal=Depends(get_principal),
):
    """Create order endpoint without trailing slash."""
    return _create_order_impl(payload, simulate, clear_cart_on_success, checkout_id, principal)


@router.post("/", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
def create_order_with_slash(
    payload: OrderCreate,
    simulate: bool = Query(False, description="True ise Aras'a istek atılmaz, sahte takip no üretilir."),
    clear_cart_on_success: bool = Query(True, description="Sipariş başarılıysa sepeti temizle."),
    checkout_id: Optional[str] = Query(
        None,
        description="Aynı checkout için tek sipariş üretmek üzere idempotent anahtar (ör. UUID).",
    ),
    principal=Depends(get_principal),
):
    """Create order endpoint with trailing slash."""
    return _create_order_impl(payload, simulate, clear_cart_on_success, checkout_id, principal)


@router.get("/my", response_model=Dict[str, List[OrderOut]])
def list_my_orders(principal=Depends(get_principal)):
    uid = extract_uid(principal)
    if not uid:
        raise HTTPException(status_code=401, detail="Oturum bulunamadı.")

    # İndeks varsa hızlı yol
    try:
        q = (
            db.collection("orders")
              .where(filter=FieldFilter("user_id", "==", uid))
              .order_by("created_at", direction=firestore.Query.DESCENDING)
              .stream()
        )
        docs = list(q)
    except FailedPrecondition:
        # İndeks yoksa: indexesiz sorgu + Python tarafı sıralama
        q = (
            db.collection("orders")
              .where(filter=FieldFilter("user_id", "==", uid))
              .stream()
        )
        docs = sorted(
            list(q),
            key=lambda d: (d.to_dict() or {}).get("created_at") or 0,
            reverse=True,
        )

    active, past = [], []
    for doc in docs:
        out = order_doc_to_out(doc)   # dict döner
        status = (out.get("status") or "").strip()
        if status in ("Teslim Edildi", "İptal"):
            past.append(out)
        else:
            active.append(out)

    return {"active": active, "past": past}



@router.get("/{order_id}", response_model=OrderOut)
def get_order_detail(order_id: str, principal=Depends(get_principal)):
    """Tekil sipariş detayını döndürür (kullanıcı kendi siparişini, admin tümünü görebilir)."""
    snap = db.collection("orders").document(order_id).get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı.")
    d = snap.to_dict()
    uid = _extract_uid(principal)
    role = getattr(principal, "role", None) or (getattr(principal, "user", {}) or {}).get("role", "user")
    if d.get("user_id") != uid and role != "admin":
        raise HTTPException(status_code=403, detail="Yetki yok.")
    return _order_doc_to_out(snap)


@router.post("/{order_id}/sync-status", response_model=OrderOut)
def sync_status_from_aras(order_id: str, principal=Depends(get_principal)):
    """
    Bu sipariş için Aras'tan durum sorgular ve Firestore'a yansıtır.
    - Simülasyon (FAKE) siparişlerde Aras'a gitmez, mevcut kaydı döndürür.
    - 'Teslim' bilgisi gelirse status='Teslim Edildi' yapılır.
    - Yeni barkod/InvoiceKey yakalanırsa tracking_number güncellenir.
    Kullanıcı kendi siparişini, admin tüm siparişleri senkron edebilir.
    """
    ref = db.collection("orders").document(order_id)
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı.")
    d = snap.to_dict() or {}

    uid = _extract_uid(principal)
    role = getattr(principal, "role", None) or (getattr(principal, "user", {}) or {}).get("role", "user")
    if d.get("user_id") != uid and role != "admin":
        raise HTTPException(status_code=403, detail="Yetki yok.")

    # Simülasyon/FAKE siparişlerde Aras'a sorgu atma
    if d.get("_simulated") or str(d.get("tracking_number") or "").startswith("FAKE-"):
        return _order_doc_to_out(snap)

    integ = d.get("integration_code")
    if not integ:
        raise HTTPException(status_code=400, detail="Sipariş entegrasyon kodu yok.")

    ok, status_text, delivered, new_track = get_status_with_integration_code(integ)
    if not ok:
        raise HTTPException(status_code=502, detail=f"Aras sorgu hatası: {status_text}")

    # Aras metnini bizim statülere eşle
    new_status = "Teslim Edildi" if delivered else _map_aras_status(status_text)

    patch = {"status": new_status, "updated_at": SERVER_TIMESTAMP}
    if new_track and new_track != d.get("tracking_number"):
        patch["tracking_number"] = new_track
    if status_text:
        patch["_last_aras_status"] = status_text

    ref.update(patch)
    return _order_doc_to_out(ref.get())




@admin_router.get("/", response_model=List[OrderOut], dependencies=[Depends(get_current_admin)])
def admin_list_orders():
    q = (
        db.collection("orders")
          .order_by("created_at", direction=firestore.Query.DESCENDING)
          .stream()
    )
    # helpers dict döndürür; FastAPI bunu OrderOut'a parse eder
    return [order_doc_to_out(doc) for doc in q]


@admin_router.post("/{order_id}/mark-delivered", response_model=OrderOut, dependencies=[Depends(get_current_admin)])
def admin_mark_delivered(order_id: str):
    """Siparişi 'Teslim Edildi' yapar."""
    ref = db.collection("orders").document(order_id)
    if not ref.get().exists:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı.")
    ref.update({"status": "Teslim Edildi", "updated_at": SERVER_TIMESTAMP})
    return _doc_to_out(ref.get())


@admin_router.post("/{order_id}/sync-status", response_model=OrderOut, dependencies=[Depends(get_current_admin)])
def admin_sync_status(order_id: str):
    """
    Aras'tan durumu çekip kaydı günceller (teslim olduysa işaretler).
    Simülasyon (FAKE) siparişlerde Aras'a gitmez, mevcut kaydı döndürür.
    """
    ref = db.collection("orders").document(order_id)
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı.")
    d = snap.to_dict() or {}

    # Simülasyon/FAKE siparişlerde Aras'a sorgu atma
    if d.get("_simulated") or str(d.get("tracking_number") or "").startswith("FAKE-"):
        return _doc_to_out(snap)

    integ = d.get("integration_code")
    if not integ:
        raise HTTPException(status_code=400, detail="integration_code yok.")

    ok, status_text, delivered, new_track = get_status_with_integration_code(integ)
    if not ok:
        raise HTTPException(status_code=502, detail=f"Aras sorgu hatası: {status_text}")

    patch = {"updated_at": SERVER_TIMESTAMP}
    patch["status"] = "Teslim Edildi" if delivered else _map_aras_status(status_text)

    if new_track and new_track != d.get("tracking_number"):
        patch["tracking_number"] = new_track
    if status_text:
        patch["_last_aras_status"] = status_text

    ref.update(patch)
    return _doc_to_out(ref.get())


@router.get("/_debug/address")
def debug_active_address(principal=Depends(get_principal)):
    uid = _extract_uid(principal)
    info: Dict[str, Any] = {"uid": uid, "steps": []}

    a1 = _fetch_active_address(uid) if uid else None
    info["steps"].append({"fetch_active_address": bool(a1)})

    a2 = None
    err2 = None
    try:
        a2 = _call_users_current_address(principal)
    except Exception as e:
        err2 = str(e)
    info["steps"].append({"users.get_current_address.ok": bool(isinstance(a2, dict) and a2.get("city")), "error": err2})

    a3 = None
    if uid:
        try:
            udoc = db.collection("users").document(uid).get()
            if udoc.exists:
                udata = udoc.to_dict() or {}
                for key in ("address", "shipping_address", "current_address"):
                    ad = udata.get(key)
                    if isinstance(ad, dict) and ad.get("city"):
                        a3 = ad
                        break
        except Exception as e:
            info["steps"].append({"users.root.error": str(e)})

    addr = a1 or (a2 if isinstance(a2, dict) else None) or a3
    return JSONResponse({
        "uid": uid,
        "address_found": bool(addr),
        "address": addr,
        "details": info["steps"],
    })


@admin_router.post("/{order_id}/label")
def admin_get_label(order_id: str):
    ref = db.collection("orders").document(order_id)
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı.")
    integ = (snap.to_dict() or {}).get("integration_code")
    if not integ:
        raise HTTPException(status_code=400, detail="integration_code yok.")
    ok, url, msg = attach_label(order_id, integ)
    if not ok:
        raise HTTPException(status_code=502, detail=msg)
    return {"label_url": url, "message": msg}



@admin_router.post("/{order_id}/mark-shipped", response_model=OrderOut, dependencies=[Depends(get_current_admin)])
def admin_mark_shipped(order_id: str):
    """
    Body gerektirmez. Sadece order_id ile statüyü 'Kargoya Verildi' yapar.
    - 'Teslim Edildi' veya 'İptal' ise değiştirmez (idempotent).
    - 'Kargoya Verildi' ise olduğu gibi döner.
    """
    ref = db.collection("orders").document(order_id)
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı.")
    d = snap.to_dict() or {}

    current = (d.get("status") or "").strip()
    if current in ("Teslim Edildi", "İptal"):
        # kapanmış siparişi zorlamayalım
        return _doc_to_out(snap)

    if current != "Kargoya Verildi":
        ref.update({"status": "Kargoya Verildi", "updated_at": SERVER_TIMESTAMP})

    return _doc_to_out(ref.get())
