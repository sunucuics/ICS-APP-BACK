"""
app/routers/carts.py
Cart endpoints (logged-in users): add by id, remove one, clear, get full cart (via /products),
and get current total (via /products).

Behavior
- Add uses ONLY product_id + quantity (no DB read).
- GET /cart fetches the current catalog from your existing /products API and returns:
  title, description, images[0], price/final_price, stock, category_name, qty, base_subtotal, total_base.
  (No discounts applied here: it mirrors the info users expect in an Amazon-style cart page.)
- GET /cart/total fetches the same catalog and returns only total_quantity and total_price,
  using final_price when provided (so if admin adds a discount → your total updates automatically).

Notes
- Prefix-aware carts collection via FIREBASE_COLLECTION_PREFIX.
- Zero unnecessary complexity; fast and predictable.
"""

import os
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import requests
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, validator

from app.core.security import get_current_user
from app.config import db

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


# ---------- catalog (via your /products API) ----------
def _fetch_products_via_api(request: Request) -> List[Dict[str, Any]]:
    """
    Calls your own /products endpoint to ensure we use the SAME IDs and fields users see in Swagger.
    Works even if products live in a prefixed or nested collection.
    """
    base = str(request.base_url).rstrip("/")
    url = f"{base}/products/"
    # If /products is public you can omit Authorization; including it is harmless.
    headers = {}
    auth = request.headers.get("authorization")
    if auth:
        headers["Authorization"] = auth
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        # Accept either a list or {"items":[...]}
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "items" in data and isinstance(data["items"], list):
            return data["items"]
        return []
    except Exception:
        # If the API call fails for any reason, return empty -> cart lines will appear unresolved instead of crashing
        return []


def _index_products_by_id(products: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """
    Build a lookup by the 'id' field coming from /products.
    """
    idx: Dict[str, Dict[str, Any]] = {}
    for p in products:
        pid = str(p.get("id", "")).strip()
        if pid:
            idx[pid] = p
    return idx


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
def clear_cart(current_user: dict = Depends(get_current_user)):
    """Clear the entire cart."""
    uid = current_user["id"]
    db.collection(_CARTS).document(uid).delete()
    return  # 204 No Content


def _get_cart_impl(request: Request, current_user: dict = Depends(get_current_user)):
    """
    Return FULL cart with product information (like Amazon):
    - title, description, image, price/final_price, stock, category_name, etc.
    - base_subtotal per item (uses final_price if present; otherwise price)
    - total_base = sum of base_subtotals (no extra discount logic here—just what /products shows)
    """
    uid = current_user["id"]
    cart = _load_cart(uid)

    # Pull what the user sees in /products (guarantees IDs match)
    products = _fetch_products_via_api(request)
    catalog = _index_products_by_id(products)

    items_out: List[Dict[str, Any]] = []
    total_qty = 0
    total_base = Decimal("0")

    for it in cart.get("items", []):
        pid = str(it.get("product_id", "")).strip()
        qty = int(it.get("qty", 0) or 0)
        if not pid or qty <= 0:
            continue

        p = catalog.get(pid)
        if not p:
            # Show unresolved row so you can spot bad IDs; don't break the whole cart
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
            "final_price": float(unit),  # uses final_price if present; else price
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
def cart_total(request: Request, current_user: dict = Depends(get_current_user)):
    """
    Return ONLY the up-to-date final total (and quantity), computed from /products.
    If admin changes a product's final_price (or price), your total here updates immediately.
    """
    uid = current_user["id"]
    cart = _load_cart(uid)

    products = _fetch_products_via_api(request)
    catalog = _index_products_by_id(products)

    total_qty = 0
    total_price = Decimal("0")

    for it in cart.get("items", []):
        pid = str(it.get("product_id", "")).strip()
        qty = int(it.get("qty", 0) or 0)
        if not pid or qty <= 0:
            continue

        p = catalog.get(pid)
        total_qty += qty

        if not p:
            # If we can't match the product in /products, treat as $0 rather than failing
            continue

        unit = _money_from_product(p)  # final_price if present, else price
        total_price += unit * qty

    return {
        "user_id": uid,
        "total_quantity": total_qty,
        "total_price": float(total_price),
    }


@router.get("")
def get_cart_no_slash(request: Request, current_user: dict = Depends(get_current_user)):
    """Get cart endpoint without trailing slash."""
    return _get_cart_impl(request, current_user)


@router.get("/")
def get_cart_with_slash(request: Request, current_user: dict = Depends(get_current_user)):
    """Get cart endpoint with trailing slash."""
    return _get_cart_impl(request, current_user)
