# backend/app/services/orders_helping.py
from __future__ import annotations

from typing import Any, Dict, List, Optional
from google.cloud.firestore_v1 import SERVER_TIMESTAMP
from firebase_admin import firestore


def resolve_active_address(db: firestore.Client, uid: str) -> Dict[str, Any]:
    """
    Eski şemayı kullanan projeler için: users/{uid}/addresses altından aktif olanı döndürür.
    """
    q = db.collection("users").document(uid).collection("addresses").where("active", "==", True).limit(1).stream()
    doc = next((d for d in q), None)
    if not doc:
        raise ValueError("Aktif adres bulunamadı")
    data = doc.to_dict() or {}
    required = ["name", "address", "city", "town", "phone"]
    missing = [k for k in required if not data.get(k)]
    if missing:
        raise ValueError(f"Adres eksik: {', '.join(missing)}")
    return {
        "receiverCustName": data["name"],
        "receiverAddress": data["address"],
        "cityName": data["city"],
        "townName": data["town"],
        "receiverPhone1": data["phone"],
    }


def find_order_by_checkout(db: firestore.Client, uid: str, checkout_id: str) -> Optional[Dict[str, Any]]:
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


def save_order(db: firestore.Client, payload: Dict[str, Any]) -> str:
    ref = db.collection("orders").document()
    payload["created_at"] = SERVER_TIMESTAMP
    ref.set(payload)
    return ref.id


def clear_cart(db: firestore.Client, uid: str) -> None:
    """
    Eski alt-koleksiyon şemasını kullanan projeler için sepet temizleyici.
    """
    items = db.collection("carts").document(uid).collection("items").stream()
    batch = db.batch()
    count = 0
    for it in items:
        batch.delete(it.reference)
        count += 1
        if count % 400 == 0:
            batch.commit()
            batch = db.batch()
    batch.commit()


def fetch_cart_items(db: firestore.Client, uid: str) -> List[Dict[str, Any]]:
    """
    carts/{uid}/items altından ürünleri çeker.
    Beklenen alanlar: product_id (str), name (str), quantity (int), price (float).
    quantity < 1 ise 1'e sabitlenir.
    """
    coll = db.collection("carts").document(uid).collection("items").stream()
    out: List[Dict[str, Any]] = []
    for d in coll:
        item = d.to_dict() or {}
        if not item.get("product_id") or not item.get("name"):
            # App Store uyumu: eksik satırı sessizce atla (veriyi zorlamıyoruz)
            continue
        q = int(item.get("quantity", 1))
        if q < 1:
            q = 1
        price = float(item.get("price", 0.0))
        out.append(
            {
                "product_id": str(item["product_id"]),
                "name": str(item["name"]),
                "quantity": q,
                "price": price,
                "meta": item.get("meta") or {},
            }
        )
    return out
