# app/routers/shipping_webhooks.py
from __future__ import annotations
from fastapi import APIRouter, Header, HTTPException, status
from typing import Dict, Any
from google.cloud.firestore_v1 import SERVER_TIMESTAMP
from backend.app.config import db, settings

router = APIRouter(prefix="/shipping", tags=["Shipping Webhooks"])

def _map_status(ar: str) -> str:
    # Aras'tan gelen metni bizim statülere eşle
    t = (ar or "").lower()
    if "teslim" in t:
        return "Teslim Edildi"
    if "dağıtım" in t:
        return "Dağıtımda"
    if "yolda" in t or "transfer" in t:
        return "Yolda"
    return "Kargoya Verildi"

@router.post("/aras")
async def aras_webhook(payload: Dict[str, Any], x_aras_signature: str | None = Header(None)):
    # Basit shared-secret doğrulaması (imza yerine)
    if settings.ARAS_WEBHOOK_SECRET and payload.get("secret") != settings.ARAS_WEBHOOK_SECRET:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid secret")

    integ = payload.get("integration_code") or payload.get("order_id")
    if not integ:
        raise HTTPException(status_code=400, detail="integration_code missing")

    status_text = payload.get("status") or payload.get("message") or ""
    track = payload.get("tracking_number")
    new_status = _map_status(status_text)

    # integration_code bizim doc id'miz; sizde farklıysa uygun field ile bulun
    q = db.collection("orders").where("integration_code", "==", integ).limit(1).get()
    if not q:
        raise HTTPException(status_code=404, detail="order not found")

    ref = q[0].reference
    patch = {"status": new_status, "_last_aras_status": status_text, "updated_at": SERVER_TIMESTAMP}
    if track:
        patch["tracking_number"] = track
    ref.update(patch)
    return {"ok": True}
