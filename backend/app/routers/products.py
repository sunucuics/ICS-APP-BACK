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
from firebase_admin import firestore
from datetime import datetime
from google.cloud.firestore_v1.field_path import FieldPath
from google.cloud.firestore_v1 import FieldFilter
from google.cloud import firestore as gcf
from google.cloud.firestore import SERVER_TIMESTAMP

router = APIRouter(prefix="/products", tags=["Products"])

def _list_products_impl(
    category_name: Optional[str] = Query(None, description="Kategori adı (opsiyonel)")
):
    """
    products/<slug>/items alt koleksiyonlarını listeler.
    - is_deleted=False
    - (ops.) category_name ile filtre
    - created_at varsa DESC sıralama
    """
    colg = db.collection_group("items")
    # Geçici olarak is_deleted filtresini kaldırıyoruz - index sorunu olabilir
    # q = colg.where(filter=FieldFilter("is_deleted", "==", False))
    q = colg

    if category_name:
        # Artık type filtresi YOK; dokümana kaydedilen category_name üzerinden filtre
        print(f"🔍 Filtering by category_name: '{category_name}'")
        # Geçici olarak filtrelemeyi kaldırıyoruz - debug için
        # q = q.where(filter=FieldFilter("category_name", "==", category_name))

    # Geçici olarak order_by'ı kaldırıyoruz - index sorunu olabilir
    # try:
    #     q = q.order_by("created_at", direction=gcf.Query.DESCENDING)
    # except Exception as e:
    #     print(f"⚠️ Order by error: {e}")
    #     pass

    out: List[ProductOut] = []
    try:
        for d in q.stream():
            src = d.to_dict() or {}
            print(f"📦 Processing product: {src.get('title', 'Unknown')} - category: {src.get('category_name', 'None')}")
            
            # Kategori filtrelemesini kod seviyesinde yap
            if category_name and src.get("category_name") != category_name:
                continue
                
            # is_deleted filtresini kod seviyesinde yap
            if src.get("is_deleted", False):
                continue
                
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
        print(f"✅ Found {len(out)} products")
    except Exception as e:
        print(f"❌ Error processing products: {e}")
        raise e
    return out


@router.get("", response_model=List[ProductOut], summary="List Products")
def list_products_no_slash(
    category_name: Optional[str] = Query(None, description="Kategori adı (opsiyonel)")
):
    """List products endpoint without trailing slash."""
    return _list_products_impl(category_name)


@router.get("/", response_model=List[ProductOut], summary="List Products")
def list_products_with_slash(
    category_name: Optional[str] = Query(None, description="Kategori adı (opsiyonel)")
):
    """List products endpoint with trailing slash."""
    return _list_products_impl(category_name)


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


@admin_router.post(
    "",
    response_model=ProductOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create Product (JSON)",
)
async def create_product_json(
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
    
    # 3) Ürün verilerini hazırla
    data = {
        "id": "",  # Firestore otomatik ID verecek
        "title": product_in.name,
        "description": product_in.description or "",
        "price": float(product_in.price),
        "final_price": float(product_in.price),  # İndirim yoksa aynı
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


@admin_router.put("/{product_id}", response_model=ProductOut)
async def update_product(product_id: str,
                         product_update: ProductUpdate):
    """
    Admin endpoint to update a product.
    Allows updating basic fields; image update can be done by uploading new images (which will replace existing images).
    If images are provided, they will overwrite the current images of the product.
    """
    # Find the product in subcollections using collection_group
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
    if product_update.title is not None: 
        update_data["title"] = product_update.title
    if product_update.description is not None: 
        update_data["description"] = product_update.description
    if product_update.price is not None: 
        update_data["price"] = product_update.price
    if product_update.stock is not None: 
        update_data["stock"] = product_update.stock
    if product_update.category_id is not None: 
        update_data["category_id"] = product_update.category_id
    if product_update.is_upcoming is not None: 
        update_data["is_upcoming"] = product_update.is_upcoming
    # Note: Image updates are handled separately via upload endpoint
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