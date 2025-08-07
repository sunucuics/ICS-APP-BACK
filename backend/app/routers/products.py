"""
app/routers/products.py - Routes for product listing (public) and product management (admin).
Handles image uploads to Firebase Storage on create.
"""
from fastapi import APIRouter, Depends, HTTPException, File, UploadFile, Form, status
from typing import List , Optional , Union
from uuid import uuid4
from app.config import db, bucket
from app.core.security import get_current_user, get_current_admin
from app.schemas.product import ProductOut , ProductCreate
from firebase_admin import firestore
from datetime import datetime
from google.cloud.firestore_v1.field_path import FieldPath

router = APIRouter(prefix="/products", tags=["Products"])

@router.get("/", response_model=List[ProductOut])
def list_products(category_name: Optional[str] = None):
    """
    Aktif (is_deleted==False) tüm ürünleri döndürür.
    - Ürünler kategori slug altındaki  `products/{slug}/items`  alt-koleksiyonlarında
      tutulduğu için **collection_group('items')** kullanıyoruz.
    - `category_name` verilirse filtre uygulanır.
    - İndirim (discounts) kontrolü yapılarak `final_price` hesaplanır.
    """
    # ➊  Koleksiyon-grup sorgusu
    base_q = (
        db.collection_group("items")
        .where("is_deleted", "==", False)
    )
    if category_name:
        base_q = base_q.where("category_name", "==", category_name)

    product_list: List[dict] = []
    now = datetime.utcnow()

    for doc in base_q.stream():
        data = doc.to_dict()
        data["id"] = doc.id

        # ➋  Aktif indirim (ürüne veya kategorisine) varsa final_price hesapla
        disc_q = (
            db.collection("discounts")
            .where("active", "==", True)
            .where("target_id", "in", [data["id"], data.get("category_id")])
            .stream()
        )

        best_percent = 0
        for d in disc_q:
            disc = d.to_dict()
            start_at, end_at = disc.get("start_at"), disc.get("end_at")
            if start_at and now < start_at:
                continue
            if end_at and now > end_at:
                continue
            best_percent = max(best_percent, disc["percent"])

        final_price = round(data["price"] * (100 - best_percent) / 100, 2)
        data["final_price"] = final_price

        product_list.append(data)

    return product_list

@router.get("/{product_id}", response_model=ProductOut)
def get_product(product_id: str):
    """
    Get details of a single product by ID.
    """
    doc = db.collection("products").document(product_id).get()
    if not doc.exists or doc.to_dict().get('is_deleted'):
        raise HTTPException(status_code=404, detail="Product not found")
    data = doc.to_dict()
    data['id'] = product_id
    # Calculate final_price similar to above
    final_price = data['price']
    disc_query = db.collection("discounts").where("active", "==", True).where("target_id", "in", [product_id, data.get('category_id')]).stream()
    best_percent = 0
    import datetime
    now = datetime.datetime.utcnow()
    for d in disc_query:
        disc = d.to_dict()
        start_at = disc.get('start_at')
        end_at = disc.get('end_at')
        if start_at and now < start_at:
            continue
        if end_at and now > end_at:
            continue
        if disc['target_type'] == 'product' and disc['target_id'] == product_id:
            best_percent = max(best_percent, disc['percent'])
        elif disc['target_type'] == 'category' and disc['target_id'] == data.get('category_id'):
            best_percent = max(best_percent, disc['percent'])
    if best_percent > 0:
        final_price = round(final_price * (100 - best_percent) / 100, 2)
    data['final_price'] = final_price
    return data

# Admin sub-router for product management
admin_router = APIRouter(prefix="/products", dependencies=[Depends(get_current_admin)])

@admin_router.post("/", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
async def create_product(
    # ───────────── product fields ─────────────
    product_in: ProductCreate = Depends(ProductCreate.as_form),
    # ───────────── photos ─────────────────────
    photo_main: UploadFile = File(
        ..., description="Zorunlu ana fotoğraf"
    ),
    photo1: UploadFile = File(
        ..., description="İsteğe bağlı foto 1 (boş bırakılabilir)"
    ),
    photo2: UploadFile = File(
        ..., description="İsteğe bağlı foto 2 (boş bırakılabilir)"
    ),
    photo3: UploadFile = File(
        ..., description="İsteğe bağlı foto 3 (boş bırakılabilir)"
    ),
    photo4: UploadFile = File(
        ..., description="İsteğe bağlı foto 4 (boş bırakılabilir)"
    ),
):
    """
    Zorunlu **bir** ana foto + en çok **4** isteğe bağlı ek foto.
    Swagger'da hepsi *Choose File* olarak görünür; kullanıcı boş bıraktıklarını yüklemez.
    """

    # 1️⃣  Boş bırakılan (Choose File yapılmamış) dosyaları ayıkla
    uploads: list[UploadFile] = [
        up for up in (photo_main, photo1, photo2, photo3, photo4) if up.filename
    ]

    if not uploads or uploads[0] is not photo_main:
        raise HTTPException(400, "photo_main zorunlu ve seçilmelidir")

    if len(uploads) > 5:
        raise HTTPException(400, "En fazla 5 foto yükleyebilirsiniz")

    # 2️⃣  Kategori dokümanı
    cat_q = (
        db.collection("categories")
        .where("name", "==", product_in.category_name)
        .where("type", "==", "product")
        .limit(1)
        .stream()
    )
    cat_doc = next(cat_q, None)
    if not cat_doc:
        raise HTTPException(404, "Kategori bulunamadı")

    cat_id = cat_doc.id
    slug = product_in.category_name.lower().replace(" ", "_")

    # 3️⃣  Ürün dokümanı
    prod_ref = db.collection(f"products/{slug}/items").document()

    # 4️⃣  Fotoğrafları Firebase Storage’a yükle
    def upload(img: UploadFile) -> str:
        fname = img.filename or f"{uuid4()}.jpg"
        blob = bucket.blob(f"products/{prod_ref.id}/{fname}")
        blob.upload_from_file(img.file, content_type=img.content_type)
        try:
            blob.make_public()
            return blob.public_url
        except Exception:
            # fallback: signed URL (10 y)
            return blob.generate_signed_url(expiration=3600 * 24 * 365 * 10)

    image_urls: List[str] = [upload(up) for up in uploads]

    # 5️⃣  Firestore’a kaydet
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