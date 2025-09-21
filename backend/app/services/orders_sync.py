# app/services/orders_sync.py
from __future__ import annotations
from firebase_admin import firestore
from google.cloud.firestore_v1 import SERVER_TIMESTAMP
from backend.app.config import db
from backend.app.integrations.shipping_provider import get_status_with_integration_code
from backend.app.schemas.order import OrderItem, OrderOut  # type hints only

OPEN_STATUSES = {"Sipariş Alındı", "Kargoya Verildi", "Yolda", "Dağıtımda"}

def sync_open_orders_once() -> int:
    """
    Açık siparişleri Aras'tan senkronlar.
    Dönüş: güncellenen kayıt sayısı.
    """
    changed = 0
    q = db.collection("orders").where("status", "in", list(OPEN_STATUSES)).stream()
    for doc in q:
        d = doc.to_dict() or {}
        integ = d.get("integration_code")
        if not integ:
            continue
        ok, status_text, delivered, new_track = get_status_with_integration_code(integ)
        if not ok:
            continue
        patch = {"_last_aras_status": status_text, "updated_at": SERVER_TIMESTAMP}
        if delivered:
            patch["status"] = "Teslim Edildi"
        elif d.get("status") == "Kargoya Verildi":
            # Aras ara durumlarını tek statüde tutuyorsanız böyle kalabilir
            pass
        if new_track and new_track != d.get("tracking_number"):
            patch["tracking_number"] = new_track
        doc.reference.update(patch)
        changed += 1
    return changed
