# backend/app/services/orders_helping.py
from __future__ import annotations

from typing import Any, Dict, List, Optional
from google.cloud.firestore_v1 import SERVER_TIMESTAMP
from firebase_admin import firestore
import os
_PREFIX = (os.getenv("FIREBASE_COLLECTION_PREFIX") or "").strip()


def _prefixed(name: str) -> str:
    return f"{_PREFIX}{name}" if _PREFIX else name
_CARTS = _prefixed("carts")


def resolve_active_address(
    db: firestore.Client,
    uid: str,
    user_doc: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """
    Aktif adresi users/{uid} dokümanındaki addresses[] + defaultAddressId'den çözer.
    Senin şemana göre adresi parçadan birleştirir.
    Dönüş: receiverCustName, receiverAddress, cityName, townName, receiverPhone1
    """
    # 1) Modern şema: users/{uid}.addresses (array) + defaultAddressId
    if user_doc:
        addresses = user_doc.get("addresses") or []
        default_id = user_doc.get("defaultAddressId")
        current = None
        if default_id:
            current = next((a for a in addresses if str(a.get("id")) == str(default_id)), None)
        if not current and addresses:
            current = addresses[0]

        if current:
            # İsim
            full_name = (
                current.get("name")
                or user_doc.get("name")
                or user_doc.get("fullName")
                or " ".join(filter(None, [user_doc.get("firstName"), user_doc.get("lastName")]))
                or ""
            )

            # Şehir / İlçe
            city = (current.get("city") or current.get("cityName") or "").strip()
            town = (
                current.get("district") or current.get("town") or current.get("townName") or ""
            ).strip()

            # Telefon: adres objesinde yoksa kullanıcı profilinden dene
            phone = (
                (current.get("phone") or current.get("phoneNumber"))
                or user_doc.get("phone")
                or user_doc.get("phoneNumber")
                or user_doc.get("gsm")
                or ""
            ).strip()

            # Adresi parçadan üret
            address_text = (
                current.get("address")
                or current.get("fullAddress")
                or " ".join(
                    p for p in [
                        current.get("neighborhood"),
                        current.get("street"),
                        f"No: {current.get('buildingNo')}" if current.get("buildingNo") else None,
                        f"Kat: {current.get('floor')}" if current.get("floor") else None,
                        f"Daire: {current.get('apartment')}" if current.get("apartment") else None,
                        current.get("zipCode"),
                    ] if p
                )
                or ""
            ).strip()

            # Zorunlu alan kontrolü
            missing = [k for k, v in {
                "name": full_name, "address": address_text, "city": city, "town": town
            }.items() if not v]
            if missing:
                raise ValueError(f"Adres eksik: {', '.join(missing)}")

            # Telefonu bulamazsak Aras genelde ister; yine de boş bırakmak istemiyorsan burada hata ver:
            if not phone:
                # İstersen default bir numara koyma; kullanıcıdan ekletmek daha doğru:
                raise ValueError("Adres için telefon bilgisi gerekli")

            return {
                "receiverCustName": full_name,
                "receiverAddress": address_text,
                "cityName": city,
                "townName": town,
                "receiverPhone1": phone,
            }

    # 2) Eski şema fallback: users/{uid}/addresses alt koleksiyonu (active==true)
    q = (
        db.collection("users")
        .document(uid)
        .collection("addresses")
        .where("active", "==", True)
        .limit(1)
        .stream()
    )
    doc = next((d for d in q), None)
    if not doc:
        raise ValueError("Aktif adres bulunamadı")

    data = doc.to_dict() or {}
    full_name = data.get("name") or data.get("fullName") or ""
    city = data.get("city") or data.get("cityName") or ""
    town = data.get("district") or data.get("town") or data.get("townName") or ""
    phone = data.get("phone") or data.get("phoneNumber") or data.get("receiverPhone1") or ""
    address_text = (
        data.get("address") or data.get("fullAddress") or ""
    ).strip()

    missing = [k for k, v in {
        "name": full_name, "address": address_text, "city": city, "town": town
    }.items() if not v]
    if missing:
        raise ValueError(f"Adres eksik: {', '.join(missing)}")
    if not phone:
        raise ValueError("Adres için telefon bilgisi gerekli")

    return {
        "receiverCustName": full_name,
        "receiverAddress": address_text,
        "cityName": city,
        "townName": town,
        "receiverPhone1": phone,
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
    Sepeti temizler. Önce doc-şemayı siler; yine de kalıntı varsa alt-koleksiyonu da temizler.
    """
    # doc-şema
    db.collection(_CARTS).document(uid).delete()

    # alt-koleksiyon temizlik (varsa)
    try:
        items = db.collection(_CARTS).document(uid).collection("items").stream()
        batch = db.batch()
        count = 0
        for it in items:
            batch.delete(it.reference)
            count += 1
            if count % 400 == 0:
                batch.commit()
                batch = db.batch()
        batch.commit()
    except Exception:
        pass


def fetch_cart_items(db: firestore.Client, uid: str) -> List[Dict[str, Any]]:
    """
    Sepeti okur. Önce doc-şemayı (carts/{uid} => {items:[{product_id, qty}]}) dener,
    boşsa alt-koleksiyon şemasına (carts/{uid}/items) geri düşer.
    Dönüşte en azından: {product_id, quantity} alanlarını garantiler.
    Ek olarak varsa name/price da dönebilir (opsiyonel).
    """
    out: List[Dict[str, Any]] = []

    # 1) DOC-BASED SCHEMA: carts/{uid} => { items: [ {product_id, qty} ] }
    snap = db.collection(_CARTS).document(uid).get()
    if snap.exists:
        data = snap.to_dict() or {}
        raw_items = data.get("items") or []
        for it in raw_items:
            pid = str(it.get("product_id") or it.get("id") or "").strip()
            qty = int(it.get("qty") or it.get("quantity") or 0)
            if pid and qty > 0:
                out.append({"product_id": pid, "quantity": qty})
        if out:
            return out  # doc şeması doluysa buradan döneriz

    # 2) SUBCOLLECTION SCHEMA: carts/{uid}/items
    try:
        coll = db.collection(_CARTS).document(uid).collection("items").stream()
        for d in coll:
            item = d.to_dict() or {}
            pid = str(item.get("product_id") or item.get("id") or d.id).strip()
            qty = int(item.get("qty") or item.get("quantity") or 0)
            if not (pid and qty > 0):
                continue
            row: Dict[str, Any] = {"product_id": pid, "quantity": qty}
            if item.get("name"):
                row["name"] = str(item["name"])
            if item.get("price") is not None:
                try:
                    row["price"] = float(item.get("price") or 0.0)
                except Exception:
                    pass
            out.append(row)
    except Exception:
        # alt-koleksiyon yoksa sessizce geç
        pass

    return out

def _uid_from_user(user: Dict[str, Any]) -> Optional[str]:
    return user.get("id") or user.get("uid") or user.get("user_id") or user.get("sub")








