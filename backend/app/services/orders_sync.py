# backend/app/services/orders_sync.py
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from firebase_admin import firestore
from google.cloud.firestore_v1 import SERVER_TIMESTAMP

from backend.app.config import db
from backend.app.integrations.shipping_provider import get_status_with_integration_code

# Senkronize edilecek "açık" durumlar (örnek isimler)
OPEN_STATUSES = {"Sipariş Alındı", "Kargoya Verildi", "Yolda", "Dağıtımda"}


def _run_async(coro):
    """
    APScheduler default executor işleri ayrı thread'de çalıştırdığı için
    burada güvenle asyncio.run kullanabiliriz. Yine de korumalı dursun.
    """
    try:
        return asyncio.run(coro)
    except RuntimeError:
        # Hali hazırda bir event loop içindeysek (nadiren),
        # geçici loop açarak çalıştır.
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


def _infer_delivered(status_text: str, data: Dict[str, Any]) -> bool:
    """
    Temel teslimat çıkarımı. İstersen burayı Aras'ın durum kodlarına göre zenginleştirebilirsin.
    """
    txt = (status_text or "").lower()
    if "teslim" in txt or "delivered" in txt:
        return True
    # Waybill veya benzeri alanlara göre de işaretlemek istersen:
    # if data.get("WaybillNo") and "delivered" in txt: ...
    return False


def _pick_tracking_number(d: Dict[str, Any]) -> Optional[str]:
    for k in ("TrackingNumber", "Barcode", "TrackingNo", "tracking_number"):
        val = d.get(k)
        if val:
            s = str(val).strip()
            if s:
                return s
    return None


def _integration_code_for(doc_id: str, d: Dict[str, Any]) -> Optional[str]:
    """
    Entegrasyon kodunu dokümandan türet.
    Öncelik: explicit integration_code > cargo_key > checkout_id > doc_id
    """
    for k in ("integration_code", "cargo_key", "checkout_id"):
        v = d.get(k)
        if v:
            s = str(v).strip()
            if s:
                return s
    return str(doc_id)


def sync_open_orders_once() -> int:
    """
    Açık siparişleri Aras'tan senkronlar.
    Dönüş: güncellenen kayıt sayısı.
    """
    changed = 0

    # Firestore: 'in' operatörü liste ister, set'i listeye çeviriyoruz.
    q = db.collection("orders").where("status", "in", list(OPEN_STATUSES)).stream()

    for doc in q:
        data = doc.to_dict() or {}

        integ = _integration_code_for(doc.id, data)
        if not integ:
            continue

        # async sağlayıcı çağrısını senkron çalıştır
        try:
            status_dict = _run_async(get_status_with_integration_code(integ))
        except Exception as e:
            # Sağlayıcı hatası varsa bu kaydı atla; job devrilmesin
            doc.reference.update({
                "_last_aras_error": str(e),
                "_last_aras_checked_at": SERVER_TIMESTAMP,
            })
            continue

        if not status_dict:
            # Kayıt bulunamadı (geçici olabilir)
            doc.reference.update({
                "_last_aras_status": "Aras: kayıt bulunamadı",
                "_last_aras_checked_at": SERVER_TIMESTAMP,
            })
            continue

        # Sağlayıcı sözlüğünden alanları toparla
        new_track = _pick_tracking_number(status_dict)
        status_text = f"Aras: kayıt bulundu (matched={status_dict.get('_matched_by')})"
        delivered = _infer_delivered(status_text, status_dict)

        patch: Dict[str, Any] = {
            "_last_aras_status": status_text,
            "_last_aras_checked_at": SERVER_TIMESTAMP,
        }

        # Teslim olduysa statüyü güncelle
        if delivered:
            patch["status"] = "Teslim Edildi"

        # Takip numarası değiştiyse güncelle
        if new_track and new_track != data.get("tracking_number"):
            patch["tracking_number"] = new_track

        # Küçük bir housekeeping: tedarikçi bazı alanları bulduysa saklayabiliriz
        for src, dst in [
            ("CargoKey", "cargo_key"),
            ("InvoiceKey", "invoice_key"),
            ("WaybillNo", "waybill_no"),
        ]:
            if status_dict.get(src) and status_dict.get(src) != data.get(dst):
                patch[dst] = status_dict[src]

        if len(patch) > 0:
            doc.reference.update(patch)
            changed += 1

    return changed
