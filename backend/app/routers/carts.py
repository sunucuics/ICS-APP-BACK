"""
app/routers/carts.py
Cart endpoints (logged-in users): add by id, remove one, clear, get full cart, get total.

Optimized: Removed self-referencing HTTP loopback (_fetch_products_via_api).
Now queries Firestore directly with batch get (db.get_all) for O(1) per product.
"""

import os
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, validator
from google.cloud.firestore_v1 import FieldFilter

from backend.app.core.security import get_current_user
from backend.app.config import db

router = APIRouter(prefix="/cart", tags=["Cart"])

# ---------- prefix-aware carts storage ----------
_PREFIX = (os.getenv("FIREBASE_COLLECTION_PREFIX") or "").strip()


def _prefixed(name: str) -> str:
    return f"{_PREFIX}{name}" if _PREFIX else name


_CARTS = _prefixed("carts")


# ---------- models ----------
class AddItemBody(BaseModel):
    """Add to cart by ID only."""
    product_id: str = Field(..., description="Product ID (the same 'id' you see in /products).")
    quantity: int = Field(1, ge=1, le=10000, description="Quantity (>=1).")

    @validator("product_id")
    def _clean_pid(cls, v: str) -> str:
        v = (v or "").strip()
        for ch in ("\u200b", "\u200c", "\u200d", "\ufeff", "\xa0"):
            v = v.replace(ch, "")
        if not v:
            raise ValueError("product_id cannot be empty")
        return v


# ---------- tiny utils ----------
def _first_image(images: Any) -> Optional[str]:
    if isinstance(images, list) and images:
        val = images[0]
        return str(val) if val is not None else None
    return None


def _money_from_product(p: Dict[str, Any]) -> Decimal:
    # Prefer final_price if present; else price
    if p.get("final_price") is not None:
        return Decimal(str(p.get("final_price", 0)))
    return Decimal(str(p.get("price", 0)))


# ---------- carts persistence ----------
def _load_cart(uid: str) -> Dict[str, Any]:
    snap = db.collection(_CARTS).document(uid).get()
    if snap.exists:
        data = snap.to_dict() or {}
        data["items"] = data.get("items", [])
        return data
    return {"items": []}


def _save_cart(uid: str, cart: Dict[str, Any]) -> None:
    db.collection(_CARTS).document(uid).set(cart)


# ---------- catalog (direct Firestore query — replaces self-referencing HTTP) ----------
def _fetch_products_by_ids(product_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """
    Fetch products directly from Firestore collection_group by their IDs.
    Uses individual queries since collection_group doesn't support get_all.
    Returns a dict keyed by product ID.
    """
    if not product_ids:
        return {}

    catalog: Dict[str, Dict[str, Any]] = {}

    # Query collection_group("items") for each unique product_id
    # Batch them: Firestore WHERE IN supports up to 30 items
    unique_ids = list(set(product_ids))

    for i in range(0, len(unique_ids), 30):
        batch_ids = unique_ids[i : i + 30]
        try:
            q = (
                db.collection_group("items")
                .where(filter=FieldFilter("id", "in", batch_ids))
                .where(filter=FieldFilter("is_deleted", "==", False))
            )
            for doc in q.stream():
                src = doc.to_dict() or {}
                pid = src.get("id", doc.id)
                if pid and pid not in catalog:
                    catalog[pid] = src
        except Exception:
            # Fallback: query one by one
            for pid in batch_ids:
                if pid in catalog:
                    continue
                try:
                    snap = next(
                        db.collection_group("items")
                        .where(filter=FieldFilter("id", "==", pid))
                        .where(filter=FieldFilter("is_deleted", "==", False))
                        .limit(1)
                        .stream(),
                        None,
                    )
                    if snap:
                        src = snap.to_dict() or {}
                        catalog[pid] = src
                except Exception:
                    pass

    return catalog


# ---------- routes ----------
@router.post("/items")
def add_to_cart(payload: AddItemBody, current_user: dict = Depends(get_current_user)):
    """
    Add product to the cart by ID only (no DB read at add time).
    """
    uid = current_user["id"]
    cart = _load_cart(uid)

    items: List[Dict[str, Any]] = cart.get("items", [])
    for it in items:
        if it.get("product_id") == payload.product_id:
            it["qty"] = int(it.get("qty", 0)) + int(payload.quantity)
            break
    else:
        items.append({"product_id": payload.product_id, "qty": int(payload.quantity)})

    cart["items"] = items
    _save_cart(uid, cart)
    cart["user_id"] = uid
    return cart


@router.delete("/items/{product_id}")
def remove_cart_item(product_id: str, current_user: dict = Depends(get_current_user)):
    """Remove one line by its stored product_id (the same id you added)."""
    uid = current_user["id"]
    cart = _load_cart(uid)
    before = len(cart.get("items", []))
    cart["items"] = [it for it in cart.get("items", []) if it.get("product_id") != product_id]
    if len(cart["items"]) == before:
        raise HTTPException(status_code=404, detail="Item not found in cart.")
    _save_cart(uid, cart)
    cart["user_id"] = uid
    return cart


@router.delete("/", status_code=204)
@router.delete("", status_code=204)
def clear_cart(current_user: dict = Depends(get_current_user)):
    """Clear the entire cart."""
    uid = current_user["id"]
    db.collection(_CARTS).document(uid).delete()
    return  # 204 No Content


def _get_cart_impl(current_user: dict):
    """
    Return FULL cart with product information.
    Optimized: fetches products directly from Firestore (no self-referencing HTTP call).
    """
    uid = current_user["id"]
    cart = _load_cart(uid)

    # Collect product IDs from cart
    cart_items = cart.get("items", [])
    product_ids = [
        str(it.get("product_id", "")).strip()
        for it in cart_items
        if str(it.get("product_id", "")).strip()
    ]

    # Fetch all products in batch
    catalog = _fetch_products_by_ids(product_ids)

    items_out: List[Dict[str, Any]] = []
    total_qty = 0
    total_base = Decimal("0")

    for it in cart_items:
        pid = str(it.get("product_id", "")).strip()
        qty = int(it.get("qty", 0) or 0)
        if not pid or qty <= 0:
            continue

        p = catalog.get(pid)
        if not p:
            items_out.append({
                "product_id": pid,
                "qty": qty,
                "unresolved": True,
                "base_subtotal": 0.0,
            })
            continue

        unit = _money_from_product(p)
        subtotal = unit * qty
        total_qty += qty
        total_base += subtotal

        items_out.append({
            "product_id": pid,
            "title": p.get("title", ""),
            "description": p.get("description", ""),
            "image": _first_image(p.get("images")),
            "category_name": p.get("category_name"),
            "stock": p.get("stock"),
            "price": float(Decimal(str(p.get("price", 0) or 0))),
            "final_price": float(unit),
            "qty": qty,
            "base_subtotal": float(subtotal),
        })

    return {
        "user_id": uid,
        "items": items_out,
        "total_quantity": total_qty,
        "total_base": float(total_base),
    }


@router.get("/total")
def cart_total(current_user: dict = Depends(get_current_user)):
    """
    Return ONLY the up-to-date final total (and quantity).
    Optimized: uses direct Firestore query instead of self-referencing HTTP.
    """
    uid = current_user["id"]
    cart = _load_cart(uid)

    cart_items = cart.get("items", [])
    product_ids = [
        str(it.get("product_id", "")).strip()
        for it in cart_items
        if str(it.get("product_id", "")).strip()
    ]

    catalog = _fetch_products_by_ids(product_ids)

    total_qty = 0
    total_price = Decimal("0")

    for it in cart_items:
        pid = str(it.get("product_id", "")).strip()
        qty = int(it.get("qty", 0) or 0)
        if not pid or qty <= 0:
            continue

        p = catalog.get(pid)
        total_qty += qty

        if not p:
            continue

        unit = _money_from_product(p)
        total_price += unit * qty

    return {
        "user_id": uid,
        "total_quantity": total_qty,
        "total_price": float(total_price),
    }


@router.get("")
def get_cart_no_slash(current_user: dict = Depends(get_current_user)):
    """Get cart endpoint without trailing slash."""
    return _get_cart_impl(current_user)


@router.get("/")
def get_cart_with_slash(current_user: dict = Depends(get_current_user)):
    """Get cart endpoint with trailing slash."""
    return _get_cart_impl(current_user)
