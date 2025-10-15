# backend/app/routers/orders.py
from __future__ import annotations
import uuid, logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from firebase_admin import firestore
from google.cloud.firestore_v1 import SERVER_TIMESTAMP

from backend.app.config import db, settings
from backend.app.core.security import get_current_user

# >>> BURAYI KULLAN (XML güvenli, çoklu SOAP denemeli client)
from backend.app.integrations.shipping_provider import (
    create_shipment_with_setorder,
    make_cargokey,
    ShippingProviderError,
)
from backend.app.integrations.aras_query_service import get_query_json

from backend.app.services.orders_helpers import (
    _uid_from_user,
    resolve_active_address,
    fetch_cart_items,
    clear_cart,
    find_order_by_checkout,
)

logger = logging.getLogger("orders")
router = APIRouter(prefix="/orders", tags=["Orders"])
admin_router = APIRouter(prefix="/orders", tags=["Orders Admin"])

@admin_router.get("/", summary="List recent orders (admin)")
def admin_list_orders(limit: int = Query(50, gt=1, le=200)):
    docs = (
        db.collection("orders")
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
        .stream()
    )
    out = []
    for d in docs:
        item = d.to_dict() or {}
        item["id"] = d.id
        out.append(item)
    return out

@router.post("/", summary="Create order → Aras Kargo gönderi")
async def create_order(
    request: Request,
    body: Optional[Dict[str, Any]] = Body(default=None),
    simulate: bool = Query(False),
    clear_cart_on_success: bool = Query(True),
    checkout_id: Optional[str] = Query(None),
    user: Dict[str, Any] = Depends(get_current_user),
):
    uid = _uid_from_user(user)
    if not uid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Kimlik doğrulama hatası")

    # Idempotent
    if checkout_id:
        existing = find_order_by_checkout(db, uid, checkout_id)
        if existing:
            return {
                "order_id": existing["id"],
                "checkout_id": existing.get("checkout_id"),
                "user_id": existing.get("user_id"),
                "status": existing.get("status", "created"),
                "tracking_number": existing.get("tracking_number"),
                "invoice_key": existing.get("invoice_key"),
                "message": "Önceden oluşturulmuş sipariş",
            }

    # Aktif adresi Firestore'dan çöz
    user_doc = db.collection("users").document(uid).get().to_dict() or {}
    try:
        addr = resolve_active_address(db, uid, user_doc)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Sepet
    items = fetch_cart_items(db, uid)
    if not items:
        raise HTTPException(status_code=400, detail="Sepet boş")

    piece_count = sum(int(i.get("quantity", 1)) for i in items)
    kg = float((body or {}).get("kg", 1.0))
    desi = float((body or {}).get("desi", 1.0))

    # Kendi entegrasyon anahtarımız (Aras'ta izlemek için)
    order_id = checkout_id or str(uuid.uuid4())
    cargo_key = make_cargokey(order_id)  # 16 hane, A-Z/0-9
    trading_waybill_number = (body or {}).get("trading_waybill_number") or cargo_key

    if simulate:
        fake_tracking = f"AR{cargo_key}"
        if clear_cart_on_success:
            clear_cart(db, uid)
        return {"order_id": None, "tracking_number": fake_tracking, "invoice_key": "", "simulated": True}

    # --- GERÇEK Aras çağrısı: XML güvenli client ---
    try:
        res = create_shipment_with_setorder(
            integration_code=cargo_key,
            trading_waybill_number=trading_waybill_number,
            receiver_name=addr.get("receiverCustName", ""),
            receiver_address=addr.get("receiverAddress", ""),
            receiver_phone1=addr.get("receiverPhone1", ""),
            receiver_city=addr.get("cityName", ""),
            receiver_town=addr.get("townName", ""),
            piece_count=piece_count,
            invoice_number=(body or {}).get("invoice_number"),
            volumetric_weight=str(desi),
            weight=str(kg),
            description=(body or {}).get("note", ""),
            # payor/is_worldwide varsayılanı .env'den gelir (shipping_provider)
        )
    except ShippingProviderError as e:
        # Aras tarafının döndürdüğü ham mesajı istersen .env EXPOSE_SHIPPING_DEBUG=1 ile göstersin
        expose = (settings.EXPOSE_SHIPPING_DEBUG is True) or (str(getattr(settings, "EXPOSE_SHIPPING_DEBUG", "0")) in ("1","true","True"))
        detail = f"Aras(SetOrder) hata: HTTP {e.http_status} {e.message}"
        if expose and e.raw:
            detail += f" | raw={e.raw[:1200]}"
        raise HTTPException(status_code=502, detail=detail)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Aras(SetOrder) beklenmeyen hata: {str(e)}")

    tracking = res.get("tracking_number") or res.get("cargo_barcode") or res.get("cargo_reference_code") or f"AR{cargo_key}"
    invoice_key = res.get("invoice_key") or ""

    # Kaydet
    order_payload = {
        "user_id": uid,
        "checkout_id": checkout_id,
        "tracking_number": tracking,
        "invoice_key": invoice_key,
        "cargo_key": cargo_key,
        "waybill_no": trading_waybill_number,
        "items": items,
        "note": (body or {}).get("note", ""),
        "status": "created",
        "created_at": SERVER_TIMESTAMP,
    }
    ref = db.collection("orders").document()
    ref.set(order_payload)

    if clear_cart_on_success:
        clear_cart(db, uid)

    return {"order_id": ref.id, "tracking_number": tracking, "invoice_key": invoice_key, "cargo_key": cargo_key}

@router.get("/{order_id}/status", summary="Sipariş durum sorgulama")
def order_status(order_id: str):
    doc = db.collection("orders").document(order_id).get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Order not found")
    order = doc.to_dict() or {}

    login_info = f"{settings.ARAS_USERNAME}|{settings.ARAS_PASSWORD}|{settings.ARAS_CUSTOMER_CODE}"
    query_info = f"<Query><Barcode>{order.get('tracking_number')}</Barcode></Query>"

    try:
        resp = get_query_json(login_info, query_info)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Sorgu hatası: {str(e)}")

    return {"raw": resp, "parsed": resp}
