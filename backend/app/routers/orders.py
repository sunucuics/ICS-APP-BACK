# backend/app/routers/orders.py
from __future__ import annotations

import os
import uuid
import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from firebase_admin import firestore
from google.cloud.firestore_v1 import SERVER_TIMESTAMP

from backend.app.config import db, settings
from backend.app.core.security import get_current_admin, get_current_user
from backend.app.integrations.shipping_provider import (
    ShippingProviderError,
    create_shipment_with_setdispatch,
    get_status_with_integration_code,
    make_cargokey,
)

logger = logging.getLogger("orders")

router = APIRouter(prefix="/orders", tags=["Orders"])

# =======================
# Ortak yardımcılar
# =======================

def _uid_from_user(user: Dict[str, Any]) -> Optional[str]:
    # get_current_user sizin modülde 'id' döndürüyor olabilir
    return user.get("id") or user.get("uid") or user.get("user_id") or user.get("sub")


def _pick(d: Dict[str, Any], *keys: str) -> Optional[str]:
    for k in keys:
        v = d.get(k)
        if v:
            s = str(v).strip()
            if s:
                return s
    return None


def _join_nonempty(parts: List[Optional[str]]) -> Optional[str]:
    cleaned: List[str] = []
    for p in parts:
        if p is None:
            continue
        s = str(p).strip()
        if s and s not in cleaned:
            cleaned.append(s)
    return " ".join(cleaned) if cleaned else None


def _resolve_current_address_local(uid: str) -> Dict[str, str]:
    """
    users/{uid} dokümanındaki defaultAddressId + addresses[] üzerinden aktif adresi çözer.
    Geriye Aras beklenen sahalarla döner.
    """
    user_ref = db.collection("users").document(uid)
    doc = user_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="User not found")

    u = doc.to_dict() or {}
    default_id = u.get("defaultAddressId")
    addresses = u.get("addresses") or []

    current = None
    if default_id:
        current = next((a for a in addresses if str(a.get("id")) == str(default_id)), None)
    if not current and addresses:
        current = addresses[0]

    if not current:
        # nadir: eski alt koleksiyon şeması
        sub_q = user_ref.collection("addresses").where("active", "==", True).limit(1).stream()
        sub_doc = next((d for d in sub_q), None)
        current = sub_doc.to_dict() if sub_doc else None

    if not current:
        raise HTTPException(status_code=400, detail="Aktif adres bulunamadı")

    full_name = (
        _pick(current, "name", "fullName", "receiverName")
        or _join_nonempty([current.get("firstName"), current.get("lastName")])
        or _pick(u, "name", "fullName")
    )
    city = _pick(current, "city", "province", "sehir", "il", "cityName")
    town = _pick(current, "town", "district", "county", "ilce", "townName")
    phone = _pick(current, "phone", "phoneNumber", "gsm", "mobile") or _pick(u, "phone", "phone_number", "gsm")

    addr_primary = _pick(
        current, "address", "fullAddress", "address1", "addressLine", "addressLine1", "streetAddress", "line1", "addr1"
    )
    addr_secondary = _pick(current, "address2", "addressLine2", "line2", "addr2")

    components = [
        current.get("neighborhood"),
        current.get("quarter"),
        current.get("mahalle"),
        current.get("street"),
        current.get("cadde"),
        current.get("sokak"),
        current.get("building"),
        current.get("buildingNo"),
        current.get("apartment"),
        current.get("blok"),
        current.get("floor"),
        current.get("kat"),
        current.get("door"),
        current.get("daire"),
        current.get("landmark"),
        current.get("zipcode"),
        current.get("zip"),
        current.get("postalCode"),
        current.get("postaKodu"),
    ]

    nested = current.get("address") if isinstance(current.get("address"), dict) else None
    if isinstance(nested, dict):
        addr_primary = addr_primary or _pick(nested, "full", "fullAddress", "line1", "address1", "addressLine1")
        addr_secondary = addr_secondary or _pick(nested, "line2", "address2", "addressLine2")
        components += [
            nested.get("neighborhood"),
            nested.get("quarter"),
            nested.get("mahalle"),
            nested.get("street"),
            nested.get("cadde"),
            nested.get("sokak"),
            nested.get("building"),
            nested.get("buildingNo"),
            nested.get("apartment"),
            nested.get("blok"),
            nested.get("floor"),
            nested.get("kat"),
            nested.get("door"),
            nested.get("daire"),
            nested.get("landmark"),
            nested.get("zipcode"),
            nested.get("zip"),
            nested.get("postalCode"),
            nested.get("postaKodu"),
        ]

    full_address = _join_nonempty([addr_primary, addr_secondary, _join_nonempty(components)])

    missing: List[str] = []
    if not full_name:
        missing.append("name")
    if not full_address:
        missing.append("address")
    if not city:
        missing.append("city")
    if not town:
        missing.append("town/district")
    if not phone:
        missing.append("phone")
    if missing:
        raise HTTPException(status_code=400, detail=f"Adres eksik: {', '.join(missing)}")

    return {
        "receiverCustName": full_name,
        "receiverAddress": full_address,
        "cityName": city,
        "townName": town,
        "receiverPhone1": phone,
    }


# =======================
# Sepet (sizin mimari) : carts/{uid}.items + /products/
# =======================

_PREFIX = (os.getenv("FIREBASE_COLLECTION_PREFIX") or "").strip()


def _prefixed(name: str) -> str:
    return f"{_PREFIX}{name}" if _PREFIX else name


_CARTS = _prefixed("carts")


def _load_cart(uid: str) -> Dict[str, Any]:
    snap = db.collection(_CARTS).document(uid).get()
    if snap.exists:
        data = snap.to_dict() or {}
        data["items"] = data.get("items", [])
        return data
    return {"items": []}


def _fetch_products_via_api(request: Request) -> List[Dict[str, Any]]:
    """
    /products/ endpoint’inizden ürün kataloğunu çeker. Authorization taşıyabilir.
    """
    base = str(request.base_url).rstrip("/")
    url = f"{base}/products/"
    headers: Dict[str, str] = {}
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth:
        headers["Authorization"] = auth
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "items" in data and isinstance(data["items"], list):
            return data["items"]
        return []
    except Exception:
        return []


def _index_products_by_id(products: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    idx: Dict[str, Dict[str, Any]] = {}
    for p in products:
        pid = str(p.get("id", "")).strip()
        if pid:
            idx[pid] = p
    return idx


def _money_from_product(p: Dict[str, Any]) -> Decimal:
    if p.get("final_price") is not None:
        return Decimal(str(p.get("final_price", 0)))
    return Decimal(str(p.get("price", 0)))


def _fetch_cart_items_local(uid: str, request: Request) -> List[Dict[str, Any]]:
    """
    carts/{uid}.items: [{product_id, qty}]
    Ürün adı/fiyat için /products/ kataloğunu kullanır. Yoksa minimum veriyle devam eder.
    """
    cart = _load_cart(uid)
    raw_items = cart.get("items", [])
    if not raw_items:
        return []

    products = _fetch_products_via_api(request)
    catalog = _index_products_by_id(products)

    out: List[Dict[str, Any]] = []
    for it in raw_items:
        pid = str(it.get("product_id", "")).strip()
        qty = int(it.get("qty", 0) or 0)
        if not pid or qty <= 0:
            continue

        p = catalog.get(pid)
        name = (p or {}).get("title") or (p or {}).get("name") or pid
        price = float(_money_from_product(p)) if p else 0.0

        out.append(
            {
                "product_id": pid,
                "name": name,
                "quantity": qty if qty > 0 else 1,
                "price": price,
            }
        )
    return out


def _clear_cart_local(uid: str) -> None:
    db.collection(_CARTS).document(uid).delete()


def _find_order_by_checkout_local(uid: str, checkout_id: str) -> Optional[Dict[str, Any]]:
    q = (
        db.collection("orders")
        .where("user_id", "==", uid)
        .where("checkout_id", "==", checkout_id)
        .limit(1)
        .stream()
    )
    doc = next((d for d in q), None)
    if not doc:
        return None
    data = doc.to_dict() or {}
    data["id"] = doc.id
    return data


def _save_order_local(payload: Dict[str, Any]) -> str:
    ref = db.collection("orders").document()
    payload["created_at"] = SERVER_TIMESTAMP
    ref.set(payload)
    return ref.id


# =======================
# CREATE ORDER
# =======================

@router.post("/", summary="Create order (reads cart automatically)")
async def create_order(
    request: Request,
    body: Optional[Dict[str, Any]] = Body(default=None),
    simulate: bool = Query(False, description="True ise Aras'a istek atılmaz, sahte takip no üretilir."),
    clear_cart_on_success: bool = Query(True, description="Sipariş başarılıysa sepeti temizle."),
    checkout_id: Optional[str] = Query(
        None, description="Aynı checkout için tek sipariş üretmek üzere idempotent anahtar (ör. UUID)."
    ),
    user: Dict[str, Any] = Depends(get_current_user),
):
    """
    - Body gelmese bile **carts/{uid}.items** içeriğini okur ve /products/ ile zenginleştirir.
    - **Idempotent**: aynı checkout_id için önceden oluşturulan siparişi döner.
    - `simulate=true`: Aras'a gitmeden takip no üretir.
    """
    uid = _uid_from_user(user)
    if not uid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Kimlik doğrulama başarısız")

    # Idempotent kontrol
    if checkout_id:
        existing = _find_order_by_checkout_local(uid, checkout_id)
        if existing:
            return {
                "order_id": existing["id"],
                "checkout_id": existing.get("checkout_id"),
                "user_id": existing.get("user_id"),
                "status": existing.get("status", "created"),
                "tracking_number": existing.get("tracking_number"),
                "provider": existing.get("provider", "aras"),
                "message": existing.get("message", "Önceden oluşturulmuş sipariş"),
                "clear_cart_on_success": bool(clear_cart_on_success),
                "created_at": str(existing.get("created_at")),
                "shipping": {
                    "tracking_number": existing.get("tracking_number"),
                    "cargo_key": existing.get("cargo_key"),
                    "invoice_key": existing.get("invoice_key"),
                    "waybill_no": existing.get("waybill_no"),
                    "message": existing.get("message", ""),
                },
            }

    # Adres ve sepet
    addr = _resolve_current_address_local(uid)
    items = _fetch_cart_items_local(uid, request)
    if not items:
        raise HTTPException(status_code=400, detail="Sepet boş. Ürün bulunamadı.")

    cargo_count = sum(int(i.get("quantity", 1)) for i in items)

    # Sipariş kimliği & CargoKey
    order_id = checkout_id or str(uuid.uuid4())
    cargo_key = make_cargokey(order_id)

    # Simülasyon
    if simulate:
        tracking = f"AR{cargo_key}"
        order_doc = {
            "checkout_id": checkout_id,
            "user_id": uid,
            "status": "created",
            "tracking_number": tracking,
            "provider": "aras",
            "message": "Simülasyon: Aras'a istek atılmadı.",
            "cargo_key": cargo_key,
            "items": items,
            "note": (body or {}).get("note"),
        }
        oid = _save_order_local(order_doc)
        if clear_cart_on_success:
            _clear_cart_local(uid)
        return {
            "order_id": oid,
            "checkout_id": checkout_id,
            "user_id": uid,
            "status": "created",
            "tracking_number": tracking,
            "provider": "aras",
            "message": "Simülasyon: Aras'a istek atılmadı.",
            "clear_cart_on_success": clear_cart_on_success,
            "shipping": {"tracking_number": tracking, "cargo_key": cargo_key, "message": "Simülasyon"},
        }

    # --- Aras SetDispatch ---
    shipping_order = {
        "CargoKey": cargo_key,
        "receiverCustName": addr["receiverCustName"],
        "receiverAddress": addr["receiverAddress"],
        "receiverPhone1": addr["receiverPhone1"],
        "cityName": addr["cityName"],
        "townName": addr["townName"],
        "cargoCount": cargo_count,
        "orgReceiverCustId": order_id,  # idempotency anahtarı
        "description": (body or {}).get("note") or "",
    }

    try:
        result = await create_shipment_with_setdispatch(shipping_order)
    except ShippingProviderError as e:
        # DEBUG modunda, Aras denemelerini (trials) da yüzeye çıkar (maskeli)
        expose = (os.getenv("EXPOSE_SHIPPING_DEBUG") or str(getattr(settings, "EXPOSE_SHIPPING_DEBUG", ""))) in ("1", "true", "True")
        detail = f"Kargo oluşturulamadı: Aras HTTP {e.http_status}: {str(e)}"
        if expose and getattr(e, "raw", None):
            detail += f" | debug={str(e.raw)[:1500]}"
        raise HTTPException(status_code=502, detail=detail)

    # Kaydet
    order_doc = {
        "checkout_id": checkout_id,
        "user_id": uid,
        "status": "created",
        "tracking_number": result.get("tracking_number"),
        "provider": "aras",
        "message": result.get("message"),
        "cargo_key": result.get("cargo_key"),
        "invoice_key": result.get("invoice_key"),
        "waybill_no": result.get("waybill_no"),
        "items": items,
        "note": (body or {}).get("note"),
    }
    oid = _save_order_local(order_doc)

    if clear_cart_on_success:
        _clear_cart_local(uid)

    return {
        "order_id": oid,
        "checkout_id": checkout_id,
        "user_id": uid,
        "status": "created",
        "tracking_number": result.get("tracking_number"),
        "provider": "aras",
        "message": result.get("message"),
        "clear_cart_on_success": clear_cart_on_success,
        "shipping": {
            "tracking_number": result.get("tracking_number"),
            "cargo_key": result.get("cargo_key"),
            "invoice_key": result.get("invoice_key"),
            "waybill_no": result.get("waybill_no"),
            "message": result.get("message"),
        },
    }


# =======================
# ADMIN
# =======================

admin_router = APIRouter(prefix="/orders", tags=["Orders Admin"], dependencies=[Depends(get_current_admin)])


@admin_router.get("/", summary="List recent orders")
def admin_list_orders(limit: int = Query(50, gt=1, le=200)):
    docs = (
        db.collection("orders")
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
        .stream()
    )
    out: List[Dict[str, Any]] = []
    for d in docs:
        item = d.to_dict() or {}
        item["id"] = d.id
        out.append(item)
    return out


@admin_router.get("/{order_id}", summary="Get order by id")
def admin_get_order(order_id: str):
    ref = db.collection("orders").document(order_id).get()
    if not ref.exists:
        raise HTTPException(status_code=404, detail="Order not found")
    data = ref.to_dict() or {}
    data["id"] = ref.id
    return data


@admin_router.post("/{order_id}/resync", summary="Refresh tracking info from Aras")
async def admin_resync_order(order_id: str):
    snap = db.collection("orders").document(order_id).get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Order not found")
    order = snap.to_dict() or {}

    integration_code = order.get("checkout_id") or order.get("cargo_key") or order_id
    status_dict = await get_status_with_integration_code(integration_code)
    if not status_dict:
        return {"status": "unchanged", "message": "Aras üzerinde kayıt bulunamadı", "id": order_id}

    updates: Dict[str, Any] = {}
    for k_src, k_dst in [
        ("TrackingNumber", "tracking_number"),
        ("CargoKey", "cargo_key"),
        ("InvoiceKey", "invoice_key"),
        ("WaybillNo", "waybill_no"),
    ]:
        if status_dict.get(k_src):
            updates[k_dst] = status_dict[k_src]

    if updates:
        db.collection("orders").document(order_id).update(updates)
        order.update(updates)

    order["id"] = order_id
    order["_sync_date"] = status_dict.get("_date")
    order["_matched_by"] = status_dict.get("_matched_by")
    return {"status": "ok", "order": order}


@admin_router.post("/_probe_setdispatch", summary="Aras SetDispatch PROBE (admin)")
async def admin_probe_setdispatch():
    """
    Aras entegrasyonunu hızlı teşhis için minimal bir gönderi dener.
    """
    cargo_key = make_cargokey("probe-" + str(uuid.uuid4()))
    shipping_order = {
        "CargoKey": cargo_key,
        "receiverCustName": "PROBE TEST",
        "receiverAddress": "TEST ADRES",
        "receiverPhone1": "02120000000",
        "cityName": os.getenv("ARAS_PROBE_CITY", "İstanbul"),
        "townName": os.getenv("ARAS_PROBE_TOWN", "Kadıköy"),
        "cargoCount": 1,
        "orgReceiverCustId": cargo_key,
        "description": "Probe",
    }
    try:
        res = await create_shipment_with_setdispatch(shipping_order)
        return {"ok": True, "result": res}
    except ShippingProviderError as e:
        return {
            "ok": False,
            "http_status": e.http_status,
            "message": str(e),
            "raw": (e.raw[:4000] if isinstance(e.raw, str) else str(e.raw)) if e.raw else None,
        }

@admin_router.post("/_aras_getquery", summary="Aras GetQueryJSON (admin test)")
def admin_aras_getquery(
    query_type: int = Query(..., ge=1, le=999),
    date: Optional[str] = Query(None, description="örn 01.10.2025"),
    date_start: Optional[str] = Query(None),
    date_end: Optional[str] = Query(None),
    barcode: Optional[str] = Query(None),
    refno: Optional[str] = Query(None),
    receiver_name: Optional[str] = Query(None),
    city: Optional[str] = Query(None),
    page: Optional[int] = Query(None, ge=1),
    page_size: Optional[int] = Query(None, ge=1, le=500),
):
    from backend.app.integrations.aras_query_service import get_query_json

    kwargs = {
        "Date": date,
        "DateStart": date_start,
        "DateEnd": date_end,
        "Barcode": barcode,
        "RefNo": refno,
        "ReceiverName": receiver_name,
        "City": city,
        "Page": page,
        "PageSize": page_size,
    }
    # None olanları temizle
    kwargs = {k: v for k, v in kwargs.items() if v is not None}

    result = get_query_json(query_type, **kwargs)
    return result