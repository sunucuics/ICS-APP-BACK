"""
app/routers/discounts.py — Admin: Ürün indirim yönetimi (form tabanlı).
Yalnızca PRODUCT hedefli indirimler desteklenir.
- Create/Update/Delete sonrasında ilgili ürünün final_price'ı yeniden hesaplanır.
- Tarihler (start_date/end_date) gün bazında; saat istenmez.
"""

from datetime import date, datetime, time, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status, Form
from google.cloud.firestore_v1 import FieldFilter

from app.config import db
from app.core.security import get_current_admin
from app.schemas.discount import DiscountCreate, DiscountUpdate, DiscountOut


# ---------------------------------------------------------------------
# Router (admin korumalı)
# ---------------------------------------------------------------------
router = APIRouter(
    prefix="/discounts",
    tags=["Discounts"],
    dependencies=[Depends(get_current_admin)],
)

# ---------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------

def _day_start_utc(d: Optional[date]) -> Optional[datetime]:
    if d is None:
        return None
    # 00:00:00 UTC
    return datetime.combine(d, time.min).replace(tzinfo=timezone.utc)

def _day_end_utc(d: Optional[date]) -> Optional[datetime]:
    if d is None:
        return None
    # 23:59:59.999999 UTC
    return datetime.combine(d, time.max).replace(tzinfo=timezone.utc)

def _is_window_active(start_at: Optional[datetime], end_at: Optional[datetime], now: datetime) -> bool:
    if start_at and now < start_at:
        return False
    if end_at and now > end_at:
        return False
    return True

def _best_discount_percent_for_product(product_id: str) -> float:
    """
    Ürüne hedeflenen (active=True) ve tarih aralığı uygun olan indirimlerden EN YÜKSEK yüzdelik.
    (Sadece target_type='product' dikkate alınır.)
    """
    now = datetime.now(timezone.utc)
    best = 0.0
    q = (
        db.collection("discounts")
        .where(filter=FieldFilter("active", "==", True))
        .where(filter=FieldFilter("target_type", "==", "product"))
        .where(filter=FieldFilter("target_id", "==", product_id))
    )
    for snap in q.stream():
        d = snap.to_dict() or {}
        if _is_window_active(d.get("start_at"), d.get("end_at"), now):
            best = max(best, float(d.get("percent", 0.0)))
    return best

def _recalc_product_final_price(product_id: str) -> None:
    """
    products/<slug>/items altındaki ürünü id ile bulur ve final_price'ı günceller.
    """
    prod = next(
        db.collection_group("items")
        .where(filter=FieldFilter("id", "==", product_id))
        .limit(1)
        .stream(),
        None,
    )
    if not prod:
        return
    pdata = prod.to_dict() or {}
    base_price = float(pdata.get("price", 0.0))
    pct = _best_discount_percent_for_product(product_id)
    new_final = round(base_price * (100.0 - pct) / 100.0, 2)
    if pdata.get("final_price") != new_final:
        prod.reference.update({"final_price": new_final})


# ---------------------------------------------------------------------
# Routes (form tabanlı)
# ---------------------------------------------------------------------

@router.get("/", response_model=List[DiscountOut], summary="List Discounts")
def list_discounts(
    product_id: Optional[str] = Query(None, description="Belirli ürün ID'sine ait indirimler"),
    active: Optional[bool] = Query(None, description="Aktif filtre"),
):
    """
    Yalnızca PRODUCT indirimlerini listeler. Opsiyonel ürün ve aktiflik filtresi.
    """
    q = db.collection("discounts").where(filter=FieldFilter("target_type", "==", "product"))
    if product_id:
        q = q.where(filter=FieldFilter("target_id", "==", product_id))
    if active is not None:
        q = q.where(filter=FieldFilter("active", "==", bool(active)))

    out: List[DiscountOut] = []
    for doc in q.stream():
        data = doc.to_dict() or {}
        out.append(DiscountOut(
            id=doc.id,
            target_type="product",
            target_id=data.get("target_id"),
            percent=float(data.get("percent", 0.0)),
            active=bool(data.get("active", False)),
            start_at=data.get("start_at"),
            end_at=data.get("end_at"),
        ))
    return out


@router.get("/{discount_id}", response_model=DiscountOut, summary="Get Discount")
def get_discount(discount_id: str):
    doc = db.collection("discounts").document(discount_id).get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Discount not found")
    data = doc.to_dict() or {}
    if data.get("target_type") != "product":
        raise HTTPException(status_code=400, detail="Only product discounts are supported")
    return DiscountOut(
        id=doc.id,
        target_type="product",
        target_id=data.get("target_id"),
        percent=float(data.get("percent", 0.0)),
        active=bool(data.get("active", False)),
        start_at=data.get("start_at"),
        end_at=data.get("end_at"),
    )


@router.post(
    "/",
    response_model=DiscountOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create Discount (product, form)",
)
def create_discount_product(
    product_id: str = Form(..., description="İndirim uygulanacak ürün ID"),
    percent: float = Form(..., description="Yüzde (0-100)"),
    start_date: Optional[date] = Form(None, description="Başlangıç (YYYY-MM-DD)"),
    end_date: Optional[date] = Form(None, description="Bitiş (YYYY-MM-DD)"),
    active: bool = Form(True, description="Aktif mi?"),
):
    """
    Yalnızca ÜRÜN indirimi oluşturur (form). Saat yok; gün bazında.
    """
    if percent <= 0 or percent > 100:
        raise HTTPException(status_code=400, detail="percent 0-100 aralığında olmalı")
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=400, detail="start_date > end_date olamaz")

    payload = {
        "target_type": "product",
        "target_id": product_id,
        "percent": float(percent),
        "active": bool(active),
        "start_at": _day_start_utc(start_date),
        "end_at": _day_end_utc(end_date),
    }

    ref = db.collection("discounts").document()
    ref.set(payload)

    # Ürünün final fiyatını güncelle
    _recalc_product_final_price(product_id)

    return DiscountOut(id=ref.id, **payload)


@router.put(
    "/{discount_id}",
    response_model=DiscountOut,
    summary="Update Discount (product, form)",
)
def update_discount_product(
    discount_id: str,
    percent: Optional[float] = Form(None, description="Yüzde (0-100)"),
    start_date: Optional[date] = Form(None, description="Başlangıç (YYYY-MM-DD)"),
    end_date: Optional[date] = Form(None, description="Bitiş (YYYY-MM-DD)"),
    active: Optional[bool] = Form(None, description="Aktif mi?"),
):
    """
    Var olan ÜRÜN indirimini günceller (form).
    """
    ref = db.collection("discounts").document(discount_id)
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Discount not found")

    current = snap.to_dict() or {}
    if current.get("target_type") != "product":
        raise HTTPException(status_code=400, detail="Only product discounts are supported")

    updates = {}
    if percent is not None:
        if percent <= 0 or percent > 100:
            raise HTTPException(status_code=400, detail="percent 0-100 aralığında olmalı")
        updates["percent"] = float(percent)
    if start_date is not None:
        updates["start_at"] = _day_start_utc(start_date)
    if end_date is not None:
        updates["end_at"] = _day_end_utc(end_date)
    if active is not None:
        updates["active"] = bool(active)

    # Tarih tutarlılığı
    if "start_at" in updates and "end_at" in updates:
        if updates["start_at"] and updates["end_at"] and updates["start_at"] > updates["end_at"]:
            raise HTTPException(status_code=400, detail="start_date > end_date olamaz")

    if updates:
        ref.update(updates)

    # Ürünün final fiyatını güncelle
    _recalc_product_final_price(current.get("target_id", ""))

    fresh = ref.get().to_dict() or {}
    return DiscountOut(id=discount_id, **fresh)


@router.delete("/{discount_id}", summary="Delete Discount")
def delete_discount(discount_id: str):
    """
    İndirimi kalıcı olarak siler ve ürünü yeniden hesaplar.
    """
    ref = db.collection("discounts").document(discount_id)
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Discount not found")

    data = snap.to_dict() or {}
    if data.get("target_type") != "product":
        raise HTTPException(status_code=400, detail="Only product discounts are supported")

    ref.delete()

    # Ürünün final fiyatını güncelle
    _recalc_product_final_price(data.get("target_id", ""))

    return {"detail": "Discount deleted"}
