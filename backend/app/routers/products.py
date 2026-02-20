"""
# `app/routers/products.py` — Ürün Yönetimi Dokümantasyonu

## Genel Bilgi
Bu dosya, herkese açık ürün listeleme/görüntüleme ve admin panelinden ürün ekleme, güncelleme, silme işlemlerini içerir.
Fotoğraf yükleme işlemleri Firebase Storage üzerinden yapılır, ürün verileri Firestore’da saklanır.

---

## Kullanıcı Tarafı Endpoint’ler

### `GET /products/`
**Amaç:** Tüm ürünleri listelemek.
**Opsiyonel Parametreler:**
- `category_name`: Kategori adına göre filtreleme

**İşleyiş:**
1. `products/<slug>/items` alt koleksiyonları `is_deleted=False` filtresi ile çekilir.
2. `category_name` verilmişse ilgili kategori ID’si bulunur, kategori yoksa boş liste döner.
3. `created_at` alanına göre azalan sıralama yapılmaya çalışılır.
4. `final_price` alanı yoksa `price` değeri atanır.
5. Liste `id` ve `final_price` ile döndürülür.

---

### `GET /products/{product_id}`
**Amaç:** Tek bir ürünün detaylarını getirmek.

**İşleyiş:**
1. `products` koleksiyonundan ürün dokümanı çekilir.
2. Yoksa veya `is_deleted=True` ise `404` döner.
3. `discounts` koleksiyonundan aktif indirimler çekilir.
4. Ürüne veya kategorisine ait en yüksek indirim oranı uygulanarak `final_price` hesaplanır.
5. Ürün bilgileri `final_price` ile döndürülür.

---

## Admin Tarafı Endpoint’ler

### `POST /products/`
**Amaç:** Yeni ürün eklemek.

**Parametreler:**
- Ürün bilgileri: `ProductCreate` (Form)
- Fotoğraflar: `photo_main` (zorunlu), `photo1`–`photo4` (opsiyonel)

**İşleyiş:**
1. En az 1 zorunlu ana fotoğraf olmalı, toplam 5’ten fazla fotoğraf yüklenemez.
2. `category_name` ile kategori ID’si bulunur (`type="product"` olmalı).
3. `products/{slug}/items` alt koleksiyonuna yeni doküman referansı oluşturulur.
4. Fotoğraflar Firebase Storage’a yüklenir, URL’leri alınır.
5. Firestore’a ürün verileri kaydedilir (`is_deleted=False`, `created_at`=timestamp).
6. Kaydedilen veri döndürülür.

---

### `PUT /products/{product_id}`
**Amaç:** Mevcut ürünü güncellemek.

**Parametreler (Form-Data):**
- `title`, `description`, `price`, `stock`, `category_id`, `is_upcoming`
- `images`: Yeni fotoğraflar (varsa mevcutlar tamamen değişir, max 5 adet)

**İşleyiş:**
1. `products` koleksiyonundan ürün dokümanı çekilir, yoksa `404` döner.
2. Gönderilen alanlar güncellenir.
3. Yeni fotoğraflar varsa Firebase Storage’a yüklenir, URL’ler güncellenir.
4. Güncel indirimler kontrol edilerek `final_price` yeniden hesaplanır.
5. Güncellenmiş ürün bilgisi döndürülür.

---

### `DELETE /products/{product_id}`
**Amaç:** Ürün silmek (soft veya hard delete).

**Parametreler:**
- `product_id`: Silinecek ürün ID’si
- `hard`: `true` ise kalıcı silme, `false` ise soft delete

**İşleyiş:**
1. Ürün `collection_group("items")` ile bulunur, yoksa `404` döner.
2. `hard=true` ise doküman tamamen silinir (isteğe bağlı görseller de silinebilir).
3. `hard=false` ise `is_deleted=True` olarak işaretlenir.
4. Silme işlemi sonucu döndürülür.

"""
from fastapi import APIRouter, Depends, HTTPException, File, UploadFile, Form, status , Query
from typing import List , Optional , Union
from uuid import uuid4
from backend.app.config import db, bucket
from backend.app.core.security import get_current_user, get_current_admin
from backend.app.schemas.product import ProductOut , ProductCreate, ProductUpdate
from backend.app.schemas.pagination import CursorPage
from firebase_admin import firestore
from datetime import datetime
from google.cloud.firestore_v1.field_path import FieldPath
from google.cloud.firestore_v1 import FieldFilter
from google.cloud import firestore as gcf
from google.cloud.firestore import SERVER_TIMESTAMP


from datetime import datetime, timezone  # mevcut importlarınızda datetime var; timezone yoksa ekleyin


def _to_aware_utc(dt):
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def _is_window_active(start_at, end_at, now):
    start_at = _to_aware_utc(start_at)
    end_at = _to_aware_utc(end_at)
    now = _to_aware_utc(now) or datetime.now(timezone.utc)
    if start_at and now < start_at:
        return False
    if end_at and now > end_at:
        return False
    return True


def _load_all_active_discounts() -> dict:
    """
    Tüm aktif indirimleri TEK SEFERDE yükler ve bir dict olarak döner.
    N+1 query sorununu çözer: ürün başına ayrı sorgu yerine tek bir toplu sorgu.

    Returns:
        {
            "product": { target_id: best_percent, ... },
            "category": { target_id: best_percent, ... },
        }
    """
    now = datetime.now(timezone.utc)
    discounts = {"product": {}, "category": {}}

    try:
        q = db.collection("discounts").where(filter=FieldFilter("active", "==", True))
        for snap in q.stream():
            d = snap.to_dict() or {}
            if not _is_window_active(d.get("start_at"), d.get("end_at"), now):
                continue
            target_type = d.get("target_type", "")
            target_id = d.get("target_id", "")
            percent = float(d.get("percent", 0.0))
            if target_type in discounts and target_id:
                discounts[target_type][target_id] = max(
                    discounts[target_type].get(target_id, 0.0), percent
                )
    except Exception:
        pass  # Discount yüklenemezse fiyatlar indirimisiz döner

    return discounts


def _calculate_final_price_from_cache(
    product_id: str,
    category_id: Optional[str],
    base_price: float,
    discount_cache: dict,
) -> float:
    """
    Önceden yüklenmiş indirim cache'i ile fiyat hesaplar (Firestore sorgusu YAPMAZ).
    """
    best_percent = 0.0

    # Product discount
    prod_pct = discount_cache.get("product", {}).get(product_id, 0.0)
    best_percent = max(best_percent, prod_pct)

    # Category discount
    if category_id:
        cat_pct = discount_cache.get("category", {}).get(category_id, 0.0)
        best_percent = max(best_percent, cat_pct)

    if best_percent > 0:
        return round(base_price * (100.0 - best_percent) / 100.0, 2)
    return round(base_price, 2)


def _calculate_final_price(product_id: str, category_id: Optional[str], base_price: float) -> float:
    """
    Ürün için indirimli fiyatı hesaplar (tek ürün detay sayfası gibi yerlerde kullanılır).
    Hem product hem de category indirimlerini kontrol eder.
    NOT: Liste endpoint'leri için _calculate_final_price_from_cache kullanın.
    """
    now = datetime.now(timezone.utc)
    best_percent = 0.0
    
    # 1) Product discount
    try:
        prod_q = (
            db.collection("discounts")
            .where(filter=FieldFilter("active", "==", True))
            .where(filter=FieldFilter("target_type", "==", "product"))
            .where(filter=FieldFilter("target_id", "==", product_id))
        )
        for snap in prod_q.stream():
            d = snap.to_dict() or {}
            if _is_window_active(d.get("start_at"), d.get("end_at"), now):
                best_percent = max(best_percent, float(d.get("percent", 0.0)))
    except Exception:
        pass  # Silently handle discount lookup errors
    
    # 2) Category discount
    if category_id:
        try:
            cat_q = (
                db.collection("discounts")
                .where(filter=FieldFilter("active", "==", True))
                .where(filter=FieldFilter("target_type", "==", "category"))
                .where(filter=FieldFilter("target_id", "==", category_id))
            )
            for snap in cat_q.stream():
                d = snap.to_dict() or {}
                if _is_window_active(d.get("start_at"), d.get("end_at"), now):
                    best_percent = max(best_percent, float(d.get("percent", 0.0)))
        except Exception:
            pass  # Silently handle discount lookup errors
    
    if best_percent > 0:
        return round(base_price * (100.0 - best_percent) / 100.0, 2)
    return round(base_price, 2)

def _delete_discounts_for_product(product_id: str) -> int:
    """
    discounts koleksiyonunda bu product_id’ye bağlı PRODUCT indirimlerini siler.
    - composite index'e takılmamak için tek filtreden ilerler.
    - eski/kirli data (targetId/productId vb.) için de temizlik yapar.
    """
    deleted = 0

    # Sadece target_type üzerinden filtre (tek alan -> index sorunsuz)
    q = db.collection("discounts").where(filter=FieldFilter("target_type", "==", "product"))

    for snap in q.stream():
        d = snap.to_dict() or {}

        # Olası alan adları
        candidates = [
            d.get("target_id"),
            d.get("targetId"),
            d.get("product_id"),
            d.get("productId"),
        ]

        if product_id in candidates:
            snap.reference.delete()
            deleted += 1

    return deleted




router = APIRouter(prefix="/products", tags=["Products"])

def _list_products_impl(
    category_name: Optional[str] = None,
    limit: int = 20,
    start_after: Optional[str] = None,
):
    """
    products/<slug>/items alt koleksiyonlarını cursor-based pagination ile listeler.
    - is_deleted=False
    - (ops.) category_name ile filtre
    - created_at DESC sıralama (Firestore seviyesinde)
    - cursor-based pagination (start_after + limit)

    Optimizasyon: Tüm aktif indirimler TEK SEFERDE yüklenir (N+1 query sorunu çözüldü).

    NOT: collection_group("items") üzerinde order_by("created_at") + start_after
    composite index gerektirir. Index yoksa Python seviyesinde sıralama + slice yapılır (fallback).
    """
    colg = db.collection_group("items")

    # Firestore sorgu filtreleri
    use_firestore_ordering = True
    try:
        q = colg.where(filter=FieldFilter("is_deleted", "==", False))
        if category_name:
            q = q.where(filter=FieldFilter("category_name", "==", category_name))

        # Firestore seviyesinde sıralama
        q = q.order_by("created_at", direction=gcf.Query.DESCENDING)

        # Cursor-based pagination: start_after varsa cursor'dan devam et
        if start_after:
            try:
                cursor_dt = datetime.fromisoformat(start_after.replace("Z", "+00:00"))
                q = q.start_after({"created_at": cursor_dt})
            except Exception:
                pass  # Geçersiz cursor → baştan başla

        # limit + 1 ile sorgula: fazladan 1 tane çekerek has_more hesapla
        docs = list(q.limit(limit + 1).stream())

    except Exception:
        # Index yoksa filtresiz devam et, Python'da filtrele ve sırala
        use_firestore_ordering = False
        q = colg
        docs = list(q.stream())

    # Tüm aktif indirimleri TEK SEFERDE yükle (N+1 sorunu çözümü)
    discount_cache = _load_all_active_discounts()

    out: List[dict] = []  # (created_at, ProductOut) — sıralama için
    for d in docs:
        src = d.to_dict() or {}

        # Firestore filtreleri çalışmazsa Python'da filtrele (fallback)
        if src.get("is_deleted", False):
            continue
        if category_name and src.get("category_name") != category_name:
            continue

        # final_price'ı cache'den hesapla (EK SORGU YOK)
        product_id = src.get("id", d.id)
        category_id = src.get("category_id")
        base_price = float(src.get("price", 0))
        calculated_final_price = _calculate_final_price_from_cache(
            product_id, category_id, base_price, discount_cache
        )

        created_at = src.get("created_at")

        out.append({
            "product": ProductOut(
                id=product_id,
                title=src.get("title", ""),
                description=src.get("description", ""),
                price=base_price,
                final_price=calculated_final_price,
                stock=int(src.get("stock", 0)),
                is_upcoming=bool(src.get("is_upcoming", False)),
                category_name=src.get("category_name", ""),
                images=src.get("images", []) or [],
            ),
            "created_at": created_at,
        })

    # Firestore sıralaması çalışmadıysa Python'da sırala ve slice et
    if not use_firestore_ordering:
        # created_at'a göre sırala (DESC)
        out.sort(
            key=lambda x: x["created_at"] if isinstance(x["created_at"], datetime) else datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )

        # Manual cursor: start_after varsa cursor'dan sonrasını al
        if start_after:
            try:
                cursor_dt = datetime.fromisoformat(start_after.replace("Z", "+00:00"))
                out = [
                    item for item in out
                    if isinstance(item["created_at"], datetime)
                    and _to_aware_utc(item["created_at"]) < _to_aware_utc(cursor_dt)
                ]
            except Exception:
                pass

        # Limit + 1 ile has_more hesapla
        has_more = len(out) > limit
        out = out[:limit]
    else:
        # Firestore sıralaması çalıştıysa limit + 1 sonucundan has_more hesapla
        has_more = len(out) > limit
        out = out[:limit]

    # next_cursor: son öğenin created_at'ı
    next_cursor = None
    if has_more and out:
        last_created_at = out[-1]["created_at"]
        if isinstance(last_created_at, datetime):
            next_cursor = last_created_at.isoformat()

    items = [item["product"] for item in out]

    return CursorPage[ProductOut](
        items=items,
        count=len(items),
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.get("", response_model=CursorPage[ProductOut], summary="List Products")
def list_products_no_slash(
    category_name: Optional[str] = Query(None, description="Kategori adı (opsiyonel)"),
    limit: int = Query(20, ge=1, le=100, description="Sayfa başına ürün sayısı"),
    start_after: Optional[str] = Query(None, description="ISO datetime cursor (önceki sayfanın next_cursor değeri)"),
):
    """List products with cursor-based pagination."""
    return _list_products_impl(category_name, limit, start_after)


@router.get("/", response_model=CursorPage[ProductOut], summary="List Products")
def list_products_with_slash(
    category_name: Optional[str] = Query(None, description="Kategori adı (opsiyonel)"),
    limit: int = Query(20, ge=1, le=100, description="Sayfa başına ürün sayısı"),
    start_after: Optional[str] = Query(None, description="ISO datetime cursor (önceki sayfanın next_cursor değeri)"),
):
    """List products with cursor-based pagination."""
    return _list_products_impl(category_name, limit, start_after)


@router.get("/{product_id}", response_model=ProductOut, summary="Get Product")
def get_product(product_id: str):
    """
    Tek ürün detayını döndürür (collection_group ile).
    """
    snap = next(
        db.collection_group("items")
          .where(filter=FieldFilter("id", "==", product_id))
          .limit(1)
          .stream(),
        None,
    )
    if not snap:
        raise HTTPException(status_code=404, detail="Product not found")

    src = snap.to_dict() or {}

    # final_price'ı dinamik olarak hesapla (indirimler dahil)
    p_id = src.get("id", snap.id)
    category_id = src.get("category_id")
    base_price = float(src.get("price", 0))
    calculated_final_price = _calculate_final_price(p_id, category_id, base_price)
    
    return ProductOut(
        id=p_id,
        title=src.get("title", ""),
        description=src.get("description", ""),
        price=base_price,
        final_price=calculated_final_price,
        stock=int(src.get("stock", 0)),
        is_upcoming=bool(src.get("is_upcoming", False)),
        category_name=src.get("category_name", ""),
        images=src.get("images", []) or [],
    )

# Admin sub-router for product management
admin_router = APIRouter(prefix="/products", dependencies=[Depends(get_current_admin)])


@admin_router.post(
    "/",
    response_model=ProductOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create Product",
    openapi_extra={  # 👈 Swagger'a dosya inputlarını zorla
        "requestBody": {
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "photo_main": {"type": "string", "format": "binary"},
                            "photo1": {"type": "string", "format": "binary"},
                            "photo2": {"type": "string", "format": "binary"},
                            "photo3": {"type": "string", "format": "binary"},
                            "photo4": {"type": "string", "format": "binary"},
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                            "price": {"type": "number"},
                            "stock": {"type": "integer"},
                            "is_upcoming": {"type": "boolean"},
                            "category_name": {"type": "string"},
                        },
                        "required": ["photo_main", "name", "price", "stock", "category_name"]
                    }
                }
            }
        }
    },
)
def create_product(
    # ürün alanları (form)
    product_in: ProductCreate = Depends(ProductCreate.as_form),
    # fotoğraflar (1 zorunlu + 4 opsiyonel)
    photo_main: UploadFile = File(..., description="Zorunlu ana fotoğraf"),
    photo1: Optional[UploadFile] = File(None, description="İsteğe bağlı foto 1"),
    photo2: Optional[UploadFile] = File(None, description="İsteğe bağlı foto 2"),
    photo3: Optional[UploadFile] = File(None, description="İsteğe bağlı foto 3"),
    photo4: Optional[UploadFile] = File(None, description="İsteğe bağlı foto 4"),
):
    # 1) Ana foto kontrolü
    if not photo_main or not photo_main.filename:
        raise HTTPException(status_code=400, detail="photo_main zorunludur")

    # 2) Opsiyonelleri topla
    optional_photos = [p for p in (photo1, photo2, photo3, photo4) if p and p.filename]
    uploads: List[UploadFile] = [photo_main] + optional_photos
    if len(uploads) > 5:
        raise HTTPException(status_code=400, detail="En fazla 5 foto yükleyebilirsiniz")

    # 3) Kategori bul (type filtresi yok)
    cat_q = (
        db.collection("categories")
          .where(filter=FieldFilter("name", "==", product_in.category_name))
          .limit(1)
          .stream()
    )
    cat_doc = next(cat_q, None)
    if not cat_doc:
        raise HTTPException(status_code=404, detail="Kategori bulunamadı")

    cat_id = cat_doc.id
    slug = product_in.category_name.lower().replace(" ", "_")

    # 4) Ürün dokümanı
    prod_ref = db.collection(f"products/{slug}/items").document()

    # 5) Fotoğrafları yükle
    def upload(img: UploadFile) -> str:
        fname = img.filename or f"{uuid4()}.jpg"
        blob = bucket.blob(f"products/{prod_ref.id}/{fname}")
        blob.upload_from_file(img.file, content_type=img.content_type)
        try:
            blob.make_public()
            return blob.public_url
        except Exception:
            return blob.generate_signed_url(expiration=3600 * 24 * 365 * 10)

    image_urls = [upload(u) for u in uploads]

    # 6) Firestore kaydı - Fiyatlar TL cinsinden saklanır
    data = product_in.model_dump()
    data.update(
        id=prod_ref.id,
        title=product_in.name,
        category_id=cat_id,
        images=image_urls,
        price=float(product_in.price),  # TL cinsinden
        final_price=float(product_in.price),  # TL cinsinden
        is_deleted=False,
        created_at=firestore.SERVER_TIMESTAMP,
    )
    prod_ref.set(data)
    return data


@admin_router.post(
    "",
    response_model=ProductOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create Product (JSON)",
)
def create_product_json(
    product_in: ProductCreate,
):
    """
    Admin endpoint to create a product via JSON (without images).
    Images can be added later via the upload endpoint.
    """
    # 1) Kategori kontrolü
    cat_ref = db.collection("categories").where("name", "==", product_in.category_name).limit(1).stream()
    cat_docs = list(cat_ref)
    if not cat_docs:
        raise HTTPException(status_code=400, detail=f"Kategori bulunamadı: {product_in.category_name}")
    cat_doc = cat_docs[0]
    cat_data = cat_doc.to_dict()
    # Kategori type kontrolü kaldırıldı - tüm kategoriler ürün kategorisi olarak kabul ediliyor

    # 2) Slug oluştur
    slug = product_in.name.lower().replace(" ", "-").replace("ı", "i").replace("ğ", "g").replace("ü", "u").replace("ş", "s").replace("ö", "o").replace("ç", "c")
    
    # 3) Ürün verilerini hazırla - Fiyatlar TL cinsinden saklanır
    data = {
        "id": "",  # Firestore otomatik ID verecek
        "title": product_in.name,
        "description": product_in.description or "",
        "price": float(product_in.price),  # TL cinsinden
        "final_price": float(product_in.price),  # İndirim yoksa aynı, TL cinsinden
        "stock": int(product_in.stock),
        "is_upcoming": bool(product_in.is_upcoming),
        "category_id": cat_doc.id,
        "category_name": product_in.category_name,
        "images": [],  # Boş başla, sonra eklenebilir
        "created_at": SERVER_TIMESTAMP,
        "updated_at": SERVER_TIMESTAMP,
    }

    # 4) Firestore'a kaydet
    prod_ref = db.collection("products").document(slug).collection("items").document()
    data["id"] = prod_ref.id
    prod_ref.set(data)
    return data


@admin_router.post("/_fix-prices", summary="Fiyatları 100'e böl (kuruş -> TL dönüşümü)")
def fix_prices_kurus_to_tl():
    """
    Tüm ürün fiyatlarını 100'e böler.
    Bu endpoint, fiyatlar yanlışlıkla kuruş cinsinden girilmişse düzeltmek için kullanılır.
    Örnek: 1000 -> 10 (TL)
    
    DİKKAT: Bu işlem geri alınamaz! Sadece gerektiğinde kullanın.
    """
    fixed_count = 0
    fixed_products = []
    
    for d in db.collection_group("items").stream():
        src = d.to_dict() or {}
        if src.get("is_deleted", False):
            continue
            
        old_price = float(src.get("price", 0))
        old_final = float(src.get("final_price", 0))
        
        # Fiyat 100'den büyükse ve kuruş olarak girilmiş olabilir
        if old_price >= 100:
            new_price = round(old_price / 100, 2)
            new_final = round(old_final / 100, 2) if old_final >= 100 else old_final
            
            d.reference.update({
                "price": new_price,
                "final_price": new_final,
            })
            
            fixed_count += 1
            fixed_products.append({
                "id": d.id,
                "title": src.get("title", "N/A"),
                "old_price": old_price,
                "new_price": new_price,
            })
    
    return {
        "message": f"{fixed_count} ürün fiyatı düzeltildi",
        "fixed_count": fixed_count,
        "fixed_products": fixed_products[:20],  # İlk 20 ürünü göster
    }


@admin_router.get("/_check-prices", summary="Fiyatları kontrol et")
def check_prices():
    """
    Tüm ürün fiyatlarını listeler - kuruş/TL tutarsızlığını tespit etmek için.
    """
    products = []
    for d in db.collection_group("items").stream():
        src = d.to_dict() or {}
        if src.get("is_deleted", False):
            continue
        
        price = float(src.get("price", 0))
        final_price = float(src.get("final_price", 0))
        
        # Fiyat analizi
        likely_kurus = price >= 100  # 100'den büyük fiyatlar muhtemelen kuruş
        
        products.append({
            "id": d.id,
            "title": src.get("title", "N/A")[:30],
            "price": price,
            "final_price": final_price,
            "likely_kurus": likely_kurus,
            "tl_if_kurus": round(price / 100, 2) if likely_kurus else None,
        })
    
    # Kuruş olabilecek ürünleri öne al
    products.sort(key=lambda x: (not x["likely_kurus"], -x["price"]))
    
    kurus_count = sum(1 for p in products if p["likely_kurus"])
    
    return {
        "total_products": len(products),
        "likely_kurus_count": kurus_count,
        "products": products[:50],  # İlk 50 ürünü göster
    }


@admin_router.put("/{product_id}", response_model=ProductOut)
def update_product(product_id: str, product_update: ProductUpdate):



    snap = next(
        db.collection_group("items")
          .where(filter=FieldFilter("id", "==", product_id))
          .limit(1)
          .stream(),
        None,
    )
    if not snap:
        raise HTTPException(status_code=404, detail="Product not found")

    doc_ref = snap.reference

    update_data = {}
    # title veya name (frontend 'name' gönderiyor)
    effective_title = product_update.title or product_update.name
    if effective_title is not None:
        update_data["title"] = effective_title
    if product_update.description is not None:
        update_data["description"] = product_update.description
    if product_update.price is not None:
        update_data["price"] = float(product_update.price)  # TL cinsinden
    if product_update.stock is not None:
        update_data["stock"] = int(product_update.stock)
    if product_update.category_id is not None:
        update_data["category_id"] = product_update.category_id
    # category_name geldiyse category_id'ye çevir
    if product_update.category_name is not None and product_update.category_id is None:
        cat_snap = next(
            db.collection("categories")
              .where(filter=FieldFilter("name", "==", product_update.category_name))
              .limit(1).stream(),
            None,
        )
        if cat_snap:
            update_data["category_id"] = cat_snap.id
    if product_update.is_upcoming is not None:
        update_data["is_upcoming"] = bool(product_update.is_upcoming)

    if update_data:
        update_data["updated_at"] = SERVER_TIMESTAMP
        doc_ref.update(update_data)

    updated_doc = doc_ref.get().to_dict() or {}
    updated_doc["id"] = product_id

    base_price = float(updated_doc.get("price", 0.0))
    category_id = updated_doc.get("category_id")

    # Reuse existing helper (single function, 1-2 queries max)
    final_price = _calculate_final_price(product_id, category_id, base_price)
    updated_doc["final_price"] = final_price

    # final_price'ı da db'ye yazalım ki listelerde tutarlı olsun
    doc_ref.update({"final_price": final_price})

    return updated_doc


@admin_router.delete("/{product_id}")
def delete_product(product_id: str, hard: bool = False):
    """
    Admin product deletion.
    
    Args:
        product_id: ID of the product to delete
        hard: If True, permanently deletes the product. If False, soft deletes (is_deleted=True)
    
    Also deletes any discounts associated with the product.
    """
    q = (
        db.collection_group("items")
        .where(filter=FieldFilter("id", "==", product_id))
        .limit(1)
        .stream()
    )
    doc_snap = next(q, None)
    if not doc_snap:
        raise HTTPException(404, "Product not found")

    doc_ref = doc_snap.reference

    # Delete associated discounts
    deleted_discounts = _delete_discounts_for_product(product_id)

    if hard:
        doc_ref.delete()
        return {"detail": "Product hard-deleted", "deleted_discounts": deleted_discounts}

    doc_ref.update({"is_deleted": True, "updated_at": SERVER_TIMESTAMP})
    return {"detail": "Product soft-deleted", "deleted_discounts": deleted_discounts}