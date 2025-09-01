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
from app.config import db, bucket
from app.core.security import get_current_user, get_current_admin
from app.schemas.product import ProductOut , ProductCreate
from firebase_admin import firestore
from datetime import datetime
from google.cloud.firestore_v1.field_path import FieldPath
from google.cloud.firestore_v1 import FieldFilter
from google.cloud import firestore as gcf

router = APIRouter(prefix="/products", tags=["Products"])

@router.get("/", response_model=List[ProductOut], summary="List Products")
def list_products(
    category_name: Optional[str] = Query(None, description="Kategori adı (opsiyonel)")
):
    """
    products/<slug>/items alt koleksiyonlarını listeler.
    - is_deleted=False
    - (ops.) category_name ile filtre
    - created_at varsa DESC sıralama
    """
    colg = db.collection_group("items")
    q = colg.where(filter=FieldFilter("is_deleted", "==", False))

    if category_name:
        # Artık type filtresi YOK; dokümana kaydedilen category_name üzerinden filtre
        q = q.where(filter=FieldFilter("category_name", "==", category_name))

    try:
        q = q.order_by("created_at", direction=gcf.Query.DESCENDING)
    except Exception:
        pass

    out: List[ProductOut] = []
    for d in q.stream():
        src = d.to_dict() or {}
        out.append(ProductOut(
            id=src.get("id", d.id),
            title=src.get("title", ""),
            description=src.get("description", ""),
            price=float(src.get("price", 0)),
            final_price=float(src.get("final_price", src.get("price", 0) or 0)),
            stock=int(src.get("stock", 0)),
            is_upcoming=bool(src.get("is_upcoming", False)),
            category_name=src.get("category_name", ""),
            images=src.get("images", []) or [],
        ))
    return out


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
    return ProductOut(
        id=src.get("id", snap.id),
        title=src.get("title", ""),
        description=src.get("description", ""),
        price=float(src.get("price", 0)),
        final_price=float(src.get("final_price", src.get("price", 0) or 0)),
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
async def create_product(
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

    # 6) Firestore kaydı
    data = product_in.model_dump()
    data.update(
        id=prod_ref.id,
        title=product_in.name,
        category_id=cat_id,
        images=image_urls,
        final_price=product_in.price,
        is_deleted=False,
        created_at=firestore.SERVER_TIMESTAMP,
    )
    prod_ref.set(data)
    return data



@admin_router.put("/{product_id}", response_model=ProductOut)
async def update_product(product_id: str,
                         title: str = Form(None),
                         description: str = Form(None),
                         price: float = Form(None),
                         stock: int = Form(None),
                         category_id: str = Form(None),
                         is_upcoming: bool = Form(None),
                         images: List[UploadFile] = File(None)):
    """
    Admin endpoint to update a product.
    Allows updating basic fields; image update can be done by uploading new images (which will replace existing images).
    If images are provided, they will overwrite the current images of the product.
    """
    doc_ref = db.collection("products").document(product_id)
    doc = doc_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Product not found")
    update_data = {}
    if title is not None: update_data["title"] = title
    if description is not None: update_data["description"] = description
    if price is not None: update_data["price"] = price
    if stock is not None: update_data["stock"] = stock
    if category_id is not None: update_data["category_id"] = category_id
    if is_upcoming is not None: update_data["is_upcoming"] = is_upcoming
    if images is not None:
        # If new images provided, we upload them and replace the old image list
        new_urls = []
        if len(images) > 5:
            raise HTTPException(status_code=400, detail="Maximum 5 images allowed")
        # Optionally, delete old images from storage (not doing here to avoid accidental data loss if needed).
        for img in images:
            filename = img.filename or f"{uuid4()}.jpg"
            blob = bucket.blob(f"products/{product_id}/{filename}")
            try:
                blob.upload_from_file(img.file, content_type=img.content_type)
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Image upload failed: {e}")
            try:
                blob.make_public()
                public_url = blob.public_url
            except Exception:
                public_url = blob.generate_signed_url(expiration=3600*24*365*10)
            new_urls.append(public_url)
        update_data["images"] = new_urls
    if update_data:
        doc_ref.update(update_data)
    # Return updated document
    updated_doc = doc_ref.get().to_dict()
    updated_doc['id'] = product_id
    # Compute final_price with any discount
    final_price = updated_doc['price']
    best_percent = 0
    import datetime
    now = datetime.datetime.utcnow()
    disc_q = db.collection("discounts").where("active", "==", True).where("target_id", "in", [product_id, updated_doc.get('category_id')]).stream()
    for d in disc_q:
        disc = d.to_dict()
        start_at = disc.get('start_at'); end_at = disc.get('end_at')
        if start_at and now < start_at: continue
        if end_at and now > end_at: continue
        if disc['target_type'] == 'product' and disc['target_id'] == product_id:
            best_percent = max(best_percent, disc['percent'])
            break  # product-specific discount found, can break
        elif disc['target_type'] == 'category' and disc['target_id'] == updated_doc.get('category_id'):
            best_percent = max(best_percent, disc['percent'])
    if best_percent:
        final_price = round(final_price * (100 - best_percent) / 100, 2)
    updated_doc['final_price'] = final_price
    return updated_doc

@admin_router.delete("/{product_id}")
def delete_product(product_id: str, hard: bool = False):
    """
    Admin product deletion
    • hard=true  → tamamen siler ve (isterseniz) görselleri de kaldırabilirsiniz
    • hard=false → is_deleted = True
    """
    # 1️⃣ ID’yi alt koleksiyonlar arasında ara
    q = (
        db.collection_group("items")
        .where("id", "==", product_id)          # id alanı ile eşleş
        .limit(1)
        .stream()
    )
    doc_snap = next(q, None)
    if not doc_snap:
        raise HTTPException(404, "Product not found")

    doc_ref = doc_snap.reference  # tam DocumentReference artık var

    # 2️⃣ İşlem
    if hard:
        # Storage’daki görseller opsiyonel olarak silinebilir
        # for blob in bucket.list_blobs(prefix=f"products/{product_id}/"):
        #     blob.delete()
        doc_ref.delete()
        return {"detail": "Product hard-deleted"}
    else:
        doc_ref.update({"is_deleted": True})
        return {"detail": "Product soft-deleted"}