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


class DiscountUpdateRequest(BaseModel):
    name: Optional[str] = None
    percentage: Optional[float] = None
    targetType: Optional[str] = None
    targetId: Optional[str] = None
    startDate: Optional[datetime] = None
    endDate: Optional[datetime] = None
    isActive: Optional[bool] = None
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

def _to_aware_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """
    Firestore'dan gelen veya request ile gelen datetime'ı UTC tz-aware hale getirir.
    - tz yoksa: UTC kabul eder.
    - tz varsa: UTC'ye çevirir.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

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
    start_at = _to_aware_utc(start_at)
    end_at = _to_aware_utc(end_at)
    now = _to_aware_utc(now) or datetime.now(timezone.utc)

    if start_at and now < start_at:
        return False
    if end_at and now > end_at:
        return False
    return True


def _best_discount_percent_for_product(product_id: str, category_id: Optional[str] = None) -> float:
    """
    Ürüne hedeflenen (active=True) ve tarih aralığı uygun olan indirimlerden EN YÜKSEK yüzdelik.
    Hem product hem de category indirimlerini kontrol eder.
    """
    now = datetime.now(timezone.utc)
    best = 0.0
    
    # 1) Product discount
    prod_q = (
        db.collection("discounts")
        .where(filter=FieldFilter("active", "==", True))
        .where(filter=FieldFilter("target_type", "==", "product"))
        .where(filter=FieldFilter("target_id", "==", product_id))
    )
    for snap in prod_q.stream():
        d = snap.to_dict() or {}
        if _is_window_active(d.get("start_at"), d.get("end_at"), now):
            best = max(best, float(d.get("percent", 0.0)))
    
    # 2) Category discount - Eğer category_id verilmemişse, ürünün category_id'sini bul
    if category_id is None:
        # Alt koleksiyonlardan ürünü bul
        item_snap = next(
            db.collection_group("items")
            .where(filter=FieldFilter("id", "==", product_id))
            .limit(1)
            .stream(),
            None,
        )
        if item_snap:
            pdata = item_snap.to_dict() or {}
            category_id = pdata.get("category_id")
    
    if category_id:
        cat_q = (
            db.collection("discounts")
            .where(filter=FieldFilter("active", "==", True))
            .where(filter=FieldFilter("target_type", "==", "category"))
            .where(filter=FieldFilter("target_id", "==", category_id))
        )
        for snap in cat_q.stream():
            d = snap.to_dict() or {}
            if _is_window_active(d.get("start_at"), d.get("end_at"), now):
                best = max(best, float(d.get("percent", 0.0)))
    
    return best


def _recalc_product_final_price(product_id: str, category_id: Optional[str] = None) -> None:
    """
    Ürünün final_price'ını günceller. Hem ana products koleksiyonunda hem de alt koleksiyonlarda günceller.
    """
    # Ana products koleksiyonunda güncelle
    prod_ref = db.collection("products").document(product_id)
    prod_doc = prod_ref.get()
    if prod_doc.exists:
        pdata = prod_doc.to_dict() or {}
        base_price = float(pdata.get("price", 0.0))
        cat_id = category_id or pdata.get("category_id")
        pct = _best_discount_percent_for_product(product_id, cat_id)
        new_final = round(base_price * (100.0 - pct) / 100.0, 2)
        if pdata.get("final_price") != new_final:
            prod_ref.update({"final_price": new_final})
    
    # Alt koleksiyonlarda da güncelle (products/{slug}/items)
    items = db.collection_group("items").where(filter=FieldFilter("id", "==", product_id)).stream()
    for item in items:
        pdata = item.to_dict() or {}
        base_price = float(pdata.get("price", 0.0))
        cat_id = category_id or pdata.get("category_id")
        pct = _best_discount_percent_for_product(product_id, cat_id)
        new_final = round(base_price * (100.0 - pct) / 100.0, 2)
        if pdata.get("final_price") != new_final:
            item.reference.update({"final_price": new_final})


def _recalc_category_products_final_price(category_id: str) -> int:
    """
    Belirli bir kategorideki TÜM ürünlerin final_price değerlerini yeniden hesaplar.
    Kategori indirimi oluşturulduğunda/güncellendiğinde/silindiğinde çağrılır.
    Returns: Güncellenen ürün sayısı
    """
    updated_count = 0
    
    # collection_group("items") ile category_id filtresi index gerektirdiğinden,
    # tüm ürünleri çekip kod seviyesinde filtreliyoruz
    try:
        items = db.collection_group("items").stream()
        
        for item in items:
            pdata = item.to_dict() or {}
            
            # Sadece bu kategorideki ürünleri işle
            if pdata.get("category_id") != category_id:
                continue
            
            product_id = pdata.get("id")
            if not product_id:
                continue
                
            # is_deleted ürünleri atla
            if pdata.get("is_deleted", False):
                continue
                
            base_price = float(pdata.get("price", 0.0))
            pct = _best_discount_percent_for_product(product_id, category_id)
            new_final = round(base_price * (100.0 - pct) / 100.0, 2)
            
            if pdata.get("final_price") != new_final:
                item.reference.update({"final_price": new_final})
                updated_count += 1
    except Exception as e:
        print(f"⚠️ Error recalculating category products: {e}")
    
    return updated_count


# ---------------------------------------------------------------------
# Routes (form tabanlı)
# ---------------------------------------------------------------------

@router.get("", response_model=List[DiscountOut], summary="List Discounts (no slash)")
def list_discounts_no_slash(
    product_id: Optional[str] = Query(None, description="Belirli ürün ID'sine ait indirimler"),
    active: Optional[bool] = Query(None, description="Aktif filtre"),
):
    """
    Tüm indirimleri listeler (product ve category). Opsiyonel ürün ve aktiflik filtresi.
    """
    q = db.collection("discounts")
    if product_id:
        q = q.where(filter=FieldFilter("target_id", "==", product_id))
    if active is not None:
        q = q.where(filter=FieldFilter("active", "==", bool(active)))

    out: List[DiscountOut] = []
    for doc in q.stream():
        data = doc.to_dict() or {}
        target_type = data.get("target_type", "product")
        # Only include product and category discounts
        if target_type in ["product", "category"]:
            target_id = data.get("target_id") or ""
            out.append(DiscountOut(
                id=doc.id,
                target_type=target_type,
                target_id=target_id,
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
    Tüm indirimleri listeler (product ve category). Opsiyonel ürün ve aktiflik filtresi.
    """
    q = db.collection("discounts")
    if product_id:
        q = q.where(filter=FieldFilter("target_id", "==", product_id))
    if active is not None:
        q = q.where(filter=FieldFilter("active", "==", bool(active)))

    out: List[DiscountOut] = []
    for doc in q.stream():
        data = doc.to_dict() or {}
        target_type = data.get("target_type", "product")
        # Only include product and category discounts
        if target_type in ["product", "category"]:
            target_id = data.get("target_id") or ""
            out.append(DiscountOut(
                id=doc.id,
                target_type=target_type,
                target_id=target_id,
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
    target_type = data.get("target_type", "product")
    if target_type not in ["product", "category"]:
        raise HTTPException(status_code=400, detail="Only product and category discounts are supported")
    return DiscountOut(
        id=doc.id,
        target_type=target_type,
        target_id=data.get("target_id") or "",
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

    start_at = _to_aware_utc(request.startDate)
    end_at = _to_aware_utc(request.endDate)
    if start_at and end_at and start_at > end_at:
        raise HTTPException(status_code=400, detail="startDate > endDate olamaz")

    payload = {
        "target_type": request.targetType,
        "target_id": request.targetId,
        "percent": float(request.percentage),
        "active": bool(request.isActive),
        "start_at": start_at,
        "end_at": end_at,
    }

    ref = db.collection("discounts").document()
    ref.set(payload)

    # Ürünlerin final fiyatını güncelle
    if request.targetId:
        if request.targetType == "product":
            _recalc_product_final_price(request.targetId)
        elif request.targetType == "category":
            # Kategori indirimi: Bu kategorideki TÜM ürünlerin fiyatlarını güncelle
            updated = _recalc_category_products_final_price(request.targetId)
            print(f"✅ Category discount created: {updated} products updated")

    return DiscountOut(
        id=ref.id,
        target_type=request.targetType,
        target_id=request.targetId or "",
        percent=float(request.percentage),
        active=bool(request.isActive),
        start_at=start_at,
        end_at=end_at,
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
    summary="Update Discount (JSON)",
)
def update_discount_json(
    discount_id: str,
    request: DiscountUpdateRequest,
):
    """
    JSON ile indirim günceller.
    """
    ref = db.collection("discounts").document(discount_id)
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Discount not found")

    current = snap.to_dict() or {}
    target_type = current.get("target_type", "product")
    if target_type not in ["product", "category"]:
        raise HTTPException(status_code=400, detail="Only product and category discounts are supported")

    updates = {}
    if request.percentage is not None:
        if request.percentage <= 0 or request.percentage > 100:
            raise HTTPException(status_code=400, detail="percentage 0-100 aralığında olmalı")
        updates["percent"] = float(request.percentage)
    if request.startDate is not None:
        updates["start_at"] = _to_aware_utc(request.startDate)
    if request.endDate is not None:
        updates["end_at"] = _to_aware_utc(request.endDate)
    if request.isActive is not None:
        updates["active"] = bool(request.isActive)
    if request.targetType is not None:
        if request.targetType not in ["product", "category"]:
            raise HTTPException(status_code=400, detail="targetType 'product' veya 'category' olmalı")
        updates["target_type"] = request.targetType
    if request.targetId is not None:
        updates["target_id"] = request.targetId

    # Tarih tutarlılığı
    start_at = updates.get("start_at") or current.get("start_at")
    end_at = updates.get("end_at") or current.get("end_at")
    if start_at and end_at and start_at > end_at:
        raise HTTPException(status_code=400, detail="start_at > end_at olamaz")

    if updates:
        ref.update(updates)
        # Update target_type if changed
        if "target_type" in updates:
            target_type = updates["target_type"]

    # Ürünlerin final fiyatlarını güncelle
    final_target_type = updates.get("target_type") or target_type
    final_target_id = updates.get("target_id") or current.get("target_id")
    old_target_type = current.get("target_type", "product")
    old_target_id = current.get("target_id")
    
    # Yeni hedef için güncelle
    if final_target_id:
        if final_target_type == "product":
            _recalc_product_final_price(final_target_id)
        elif final_target_type == "category":
            updated = _recalc_category_products_final_price(final_target_id)
            print(f"✅ Category discount updated: {updated} products updated")
    
    # Eski hedef değiştiyse, eski hedefi de güncelle
    if old_target_id and old_target_id != final_target_id:
        if old_target_type == "product":
            _recalc_product_final_price(old_target_id)
        elif old_target_type == "category":
            updated = _recalc_category_products_final_price(old_target_id)
            print(f"✅ Old category products updated: {updated} products")

    fresh = ref.get().to_dict() or {}
    final_target_type = fresh.get("target_type", target_type)
    return DiscountOut(
        id=discount_id,
        target_type=final_target_type,
        target_id=fresh.get("target_id") or "",
        percent=float(fresh.get("percent", 0.0)),
        active=bool(fresh.get("active", False)),
        start_at=fresh.get("start_at"),
        end_at=fresh.get("end_at"),
    )




@router.put(
    "/{discount_id}/form",
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
    target_type = current.get("target_type", "product")
    if target_type not in ["product", "category"]:
        raise HTTPException(status_code=400, detail="Only product and category discounts are supported")

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

    # Ürünlerin final fiyatlarını güncelle
    target_id = current.get("target_id", "")
    if target_id:
        if target_type == "product":
            _recalc_product_final_price(target_id)
        elif target_type == "category":
            updated = _recalc_category_products_final_price(target_id)
            print(f"✅ Category discount updated (form): {updated} products updated")

    fresh = ref.get().to_dict() or {}
    return DiscountOut(
        id=discount_id,
        target_type=target_type,
        target_id=fresh.get("target_id") or "",
        percent=float(fresh.get("percent", 0.0)),
        active=bool(fresh.get("active", False)),
        start_at=fresh.get("start_at"),
        end_at=fresh.get("end_at"),
    )


@router.delete("/{discount_id}", summary="Delete Discount")
def delete_discount(discount_id: str):
    """
    İndirimi kalıcı olarak siler ve ürünlerin final fiyatlarını yeniden hesaplar.
    """
    ref = db.collection("discounts").document(discount_id)
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Discount not found")

    data = snap.to_dict() or {}
    target_type = data.get("target_type", "product")
    if target_type not in ["product", "category"]:
        raise HTTPException(status_code=400, detail="Only product and category discounts are supported")

    target_id = data.get("target_id", "")
    
    # İndirimi sil
    ref.delete()

    # Ürünlerin final fiyatlarını güncelle (hata olsa bile response dön)
    try:
        if target_id:
            if target_type == "product":
                _recalc_product_final_price(target_id)
            elif target_type == "category":
                # Kategori indirimi silindi: Bu kategorideki TÜM ürünlerin fiyatlarını güncelle
                updated = _recalc_category_products_final_price(target_id)
                print(f"✅ Category discount deleted: {updated} products updated")
    except Exception as e:
        # Fiyat güncelleme hatası olsa bile indirim silindi, sadece logla
        print(f"⚠️ Warning: Discount deleted but price recalculation failed: {e}")
        # Response yine de başarılı dön (indirim silindi)

    return {"detail": "Discount deleted"}
