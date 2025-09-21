"""
app/routers/discounts.py — Admin: Ürün indirim yönetimi (form tabanlı).
Yalnızca PRODUCT hedefli indirimler desteklenir.
- Create/Update/Delete sonrasında ilgili ürünün final_price'ı yeniden hesaplanır.
- Tarihler (start_date/end_date) gün bazında; saat istenmez.
"""

from datetime import date, datetime, time, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status, Form
from pydantic import BaseModel
from google.cloud.firestore_v1 import FieldFilter

from backend.app.config import db
from backend.app.core.security import get_current_admin
from backend.app.schemas.discount import DiscountCreate, DiscountUpdate, DiscountOut


# ---------------------------------------------------------------------
# JSON Request Models
# ---------------------------------------------------------------------

class DiscountCreateRequest(BaseModel):
    name: str
    percentage: float
    targetType: str
    targetId: Optional[str] = None
    startDate: Optional[datetime] = None
    endDate: Optional[datetime] = None
    isActive: bool = True
    description: Optional[str] = None


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
    Ürünün final_price'ını günceller. Hem ana products koleksiyonunda hem de alt koleksiyonlarda günceller.
    """
    # Ana products koleksiyonunda güncelle
    prod_ref = db.collection("products").document(product_id)
    prod_doc = prod_ref.get()
    if prod_doc.exists:
        pdata = prod_doc.to_dict() or {}
        base_price = float(pdata.get("price", 0.0))
        pct = _best_discount_percent_for_product(product_id)
        new_final = round(base_price * (100.0 - pct) / 100.0, 2)
        if pdata.get("final_price") != new_final:
            prod_ref.update({"final_price": new_final})
    
    # Alt koleksiyonlarda da güncelle (products/{slug}/items)
    items = db.collection_group("items").where(filter=FieldFilter("id", "==", product_id)).stream()
    for item in items:
        pdata = item.to_dict() or {}
        base_price = float(pdata.get("price", 0.0))
        pct = _best_discount_percent_for_product(product_id)
        new_final = round(base_price * (100.0 - pct) / 100.0, 2)
        if pdata.get("final_price") != new_final:
            item.reference.update({"final_price": new_final})


# ---------------------------------------------------------------------
# Routes (form tabanlı)
# ---------------------------------------------------------------------

@router.get("", response_model=List[DiscountOut], summary="List Discounts (no slash)")
def list_discounts_no_slash(
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
    "",
    response_model=DiscountOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create Discount (JSON, no slash)",
)
def create_discount_json_no_slash(request: DiscountCreateRequest):
    """
    JSON ile indirim oluşturur.
    """
    if request.percentage <= 0 or request.percentage > 100:
        raise HTTPException(status_code=400, detail="percentage 0-100 aralığında olmalı")
    
    if request.targetType not in ["product", "category"]:
        raise HTTPException(status_code=400, detail="targetType 'product' veya 'category' olmalı")
    
    if request.startDate and request.endDate and request.startDate > request.endDate:
        raise HTTPException(status_code=400, detail="startDate > endDate olamaz")

    payload = {
        "target_type": request.targetType,
        "target_id": request.targetId,
        "percent": float(request.percentage),
        "active": bool(request.isActive),
        "start_at": request.startDate,
        "end_at": request.endDate,
    }

    ref = db.collection("discounts").document()
    ref.set(payload)

    # Ürünün final fiyatını güncelle (sadece product için)
    if request.targetType == "product" and request.targetId:
        _recalc_product_final_price(request.targetId)

    return DiscountOut(id=ref.id, **payload)


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
