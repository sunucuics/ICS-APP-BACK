# backend/app/routers/shipping.py
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/shipping", tags=["Shipping"])

@router.get("/label/{invoice_key}", summary="(WIP) Kargo etiketi PDF")
def get_label(invoice_key: str):
    # Aras'ta etiket almak ayrı bir endpoint/operasyon gerektirir.
    # Şimdilik 501 döndürüyoruz; ihtiyaç olunca Aras'ın 'GetLabel' benzeri operasyonunu bağlarız.
    raise HTTPException(status_code=501, detail="Label endpoint henüz uygulanmadı")
