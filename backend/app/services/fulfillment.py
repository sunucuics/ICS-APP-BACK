# app/services/fulfillment.py
from __future__ import annotations
from datetime import date, timedelta
from typing import Optional, Tuple

from firebase_admin import storage
from google.cloud.firestore_v1 import SERVER_TIMESTAMP

from backend.app.config import db, settings
from backend.app.integrations.shipping_provider import (
    get_label_pdf,            # (ok, filename, pdf_bytes, msg)
    request_pickup,           # (ok, pickup_id_or_msg)
)
# Not: create_shipment_with_setorder ve get_status_with_integration_code zaten mevcut.

def _upload_pdf_and_get_url(path: str, pdf_bytes: bytes) -> str:
    """PDF'yi Firebase Storage'a yükleyip URL döndürür (public veya imzalı)."""
    bucket = storage.bucket()
    blob = bucket.blob(path)
    blob.upload_from_string(pdf_bytes, content_type="application/pdf")
    if settings.LABEL_PUBLIC:
        blob.make_public()
        return blob.public_url
    # imzalı (zaman kısıtlı) URL
    return blob.generate_signed_url(expiration=timedelta(hours=settings.LABEL_URL_EXPIRES_HOURS))

def attach_label(order_id: str, integration_code: str) -> Tuple[bool, Optional[str], str]:
    """
    Aras'tan etiket PDF'yi çeker, Storage'a yükler ve order kaydına yazar.
    Dönüş: (ok, url, msg)
    """
    ok, filename, pdf_bytes, msg = get_label_pdf(integration_code)
    if not ok or not pdf_bytes:
        return False, None, msg or "Label not available"
    storage_path = f"shipments/{order_id}/{filename or 'label.pdf'}"
    url = _upload_pdf_and_get_url(storage_path, pdf_bytes)
    db.collection("orders").document(order_id).update({
        "label_url": url,
        "label_file": filename or "label.pdf",
        "updated_at": SERVER_TIMESTAMP
    })
    return True, url, "Label attached"

def schedule_pickup(order_id: str, integration_code: str) -> Tuple[bool, str]:
    """
    Aras'ta kurye alımı talep eder ve order kaydına işler.
    """
    pickup_date = date.today() + timedelta(days=int(settings.PICKUP_DAYS_OFFSET or 0))
    window = settings.PICKUP_TIME_WINDOW
    ok, pickup_ref = request_pickup(integration_code=integration_code,
                                    pickup_date=pickup_date,
                                    time_window=window)
    db.collection("orders").document(order_id).update({
        "pickup": {
            "date": pickup_date.isoformat(),
            "window": window,
            "ref": pickup_ref if ok else None,
            "ok": ok,
        },
        "updated_at": SERVER_TIMESTAMP
    })
    return ok, pickup_ref or "pickup requested"

def auto_after_create(order_id: str, integration_code: str) -> None:
    """
    Sipariş başarıyla oluşturulduktan hemen sonra:
    - AUTO_LABEL True ise etiketi çek ve kaydet
    - AUTO_PICKUP True ise kurye iste
    """
    if getattr(settings, "AUTO_LABEL", False):
        try:
            attach_label(order_id, integration_code)
        except Exception:
            pass
    if getattr(settings, "AUTO_PICKUP", False):
        try:
            schedule_pickup(order_id, integration_code)
        except Exception:
            pass
