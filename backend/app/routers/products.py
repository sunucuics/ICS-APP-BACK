"""
app/routers/products.py - Routes for product listing (public) and product management (admin).
Handles image uploads to Firebase Storage on create.
"""
from fastapi import APIRouter, Depends, HTTPException, File, UploadFile, Form, status
from typing import List
from uuid import uuid4
from app.config import db, bucket
from app.core.security import get_current_user, get_current_admin
from app.schemas.product import ProductOut

router = APIRouter(prefix="/products", tags=["Products"])

@router.get("/", response_model=List[ProductOut])
def list_products(category_id: str = None):
    """
    Get list of all active products, optionally filtered by category.
    Upcoming products are included (with flag) but not purchasable.
    Soft-deleted products are excluded.
    """
    products_ref = db.collection("products")
    query = products_ref.where("is_deleted", "==", False) if products_ref is not None else None
    if category_id:
        query = query.where("category_id", "==", category_id) if query else products_ref.where("category_id", "==", category_id)
    docs = (query or products_ref).stream()
    product_list = []
    # We may also fetch discount info to compute final_price
    # For simplicity, we will compute final_price by checking discount collection for each product.
    discounts = []
    for doc in docs:
        data = doc.to_dict()
        if data.get('is_deleted'):
            continue  # skip any soft-deleted (should already be filtered by query)
        data['id'] = doc.id
        # Determine final price after discount
        final_price = data['price']
        # Check if a discount applies (either directly or via category)
        disc_query = db.collection("discounts").where("active", "==", True).where("target_id", "in", [doc.id, data.get('category_id')]).stream()
        # We query discounts where target_id matches this product or its category
        best_percent = 0
        for d in disc_query:
            disc = d.to_dict()
            # Ensure discount is currently valid (time-based)
            import datetime
            now = datetime.datetime.utcnow()
            start_at = disc.get('start_at')
            end_at = disc.get('end_at')
            if start_at and now < start_at:
                continue  # not started yet
            if end_at and now > end_at:
                continue  # expired
            if disc['target_type'] == 'product' and disc['target_id'] == doc.id:
                best_percent = max(best_percent, disc['percent'])
            elif disc['target_type'] == 'category' and disc['target_id'] == data.get('category_id'):
                best_percent = max(best_percent, disc['percent'])
        if best_percent > 0:
            final_price = round(final_price * (100 - best_percent) / 100, 2)
        data['final_price'] = final_price
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
    title: str = Form(...),
    description: str = Form(""),
    price: float = Form(...),
    stock: int = Form(...),
    category_id: str = Form(...),
    is_upcoming: bool = Form(False),
    images: List[UploadFile] = File(None)
):
    """
    Admin endpoint to create a new product with optional image uploads.
    Accepts form data: title, description, price, stock, category_id, is_upcoming, and up to 5 image files.
    """
    # Create product document in Firestore
    product_ref = db.collection("products").document()
    product_id = product_ref.id
    product_data = {
        "title": title,
        "description": description,
        "price": price,
        "stock": stock,
        "category_id": category_id,
        "is_upcoming": is_upcoming,
        "is_deleted": False,
        "created_at": None  # use server timestamp if desired
    }
    # Handle images upload
    image_urls = []
    if images:
        if len(images) > 5:
            raise HTTPException(status_code=400, detail="Maximum 5 images allowed")
        for img in images:
            # Only process if it's an image
            filename = img.filename
            # Construct storage path: e.g., products/<product_id>/<filename>
            blob = bucket.blob(f"products/{product_id}/{filename}")
            try:
                blob.upload_from_file(img.file, content_type=img.content_type)
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Image upload failed: {e}")
            # Make the blob publicly accessible (or we could generate a tokened URL)
            try:
                blob.make_public()
                public_url = blob.public_url
            except Exception:
                # If make_public is not allowed due to security rules, use signed URL as fallback
                public_url = blob.generate_signed_url(expiration=3600*24*365*10)  # 10-year URL
            image_urls.append(public_url)
    product_data["images"] = image_urls
    product_ref.set(product_data)
    product_data["id"] = product_id
    # Compute final_price (no discount initially on brand new product)
    product_data["final_price"] = product_data["price"]
    return product_data

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
    Admin endpoint to delete a product.
    If hard=true, permanently deletes the product from Firestore (and optionally its images from storage).
    If hard=false, performs a soft delete (sets is_deleted=true).
    """
    doc_ref = db.collection("products").document(product_id)
    doc = doc_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Product not found")
    if hard:
        # Delete storage files (optional; not doing automatically to avoid accidental deletion)
        # Actually perform Firestore delete:
        doc_ref.delete()
        # (Optionally, we might also delete all comments related to this product, etc., if needed.)
    else:
        doc_ref.update({"is_deleted": True})
    return {"detail": f"Product {'hard ' if hard else ''}deleted"}
