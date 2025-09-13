# app/routers/categories.py
"""
Kategori Yönetimi
- Public: GET /categories/  → ürün kategorilerini (silinmemiş) listeler
- Admin : /admin/categories → oluşturma/güncelleme/silme
"""

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, Response
from typing import List, Optional
from uuid import uuid4

from app.config import db, bucket
from app.core.security import get_current_admin
from app.schemas.category import CategoryCreate, CategoryUpdate, CategoryOut

from firebase_admin import firestore
from google.cloud.firestore_v1 import FieldFilter
from google.cloud import firestore as gcf  # Query.DESCENDING

# ---------- Public ----------
router = APIRouter(prefix="/categories", tags=["Categories"])

def _list_categories_impl(response: Response):
    """
    Ürün kategorilerini listeler.
    - Sadece `is_deleted=False` kayıtlar
    - `created_at` varsa DESC sıralama
    - Varsayılan limit: 50
    """
    col = db.collection("categories")
    q = col.where(filter=FieldFilter("is_deleted", "==", False))
    try:
        q = q.order_by("created_at", direction=gcf.Query.DESCENDING)
    except Exception:
        # created_at olmayan eski kayıtlar için sıralama yutulur
        pass

    docs = list(q.limit(50).stream())
    response.headers["Cache-Control"] = "public, max-age=60"

    out: List[CategoryOut] = []
    for d in docs:
        data = d.to_dict() or {}
        out.append(CategoryOut(
            id=d.id,
            name=data.get("name", ""),
            description=data.get("description", ""),
            parent_id=data.get("parent_id"),
            cover_image=data.get("cover_image")
        ))
    return out

# ---------- Admin ----------
admin_router = APIRouter(
    prefix="/categories",
    tags=["Admin: Categories"],
    dependencies=[Depends(get_current_admin)]
)

@admin_router.post(
    "",
    response_model=CategoryOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create Category"
)
async def create_category(
    category_in: CategoryCreate = Depends(CategoryCreate.as_form),
    cover_image: UploadFile = File(..., description="Kapak görseli (zorunlu)")
):
    """
    Yeni **ürün** kategorisi oluşturur.
    Kapak görseli Firebase Storage'a yüklenir, URL'i kaydedilir.
    """
    doc_ref = db.collection("categories").document()

    # Görseli yükle
    filename = cover_image.filename or f"{uuid4()}.jpg"
    blob = bucket.blob(f"categories/{doc_ref.id}/{filename}")
    try:
        blob.upload_from_file(cover_image.file, content_type=cover_image.content_type)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Image upload failed: {e}")

    # Public URL ya da uzun vadeli signed URL
    try:
        blob.make_public()
        image_url = blob.public_url
    except Exception:
        image_url = blob.generate_signed_url(expiration=3600 * 24 * 365 * 10)

    payload = {
        "id": doc_ref.id,
        "name": category_in.name.strip(),
        "description": (category_in.description or "").strip(),
        "parent_id": category_in.parent_id,
        "cover_image": image_url,
        "is_deleted": False,
        "kind": "category",
        "created_at": firestore.SERVER_TIMESTAMP,
    }
    doc_ref.set(payload)

    snap = doc_ref.get()
    data = snap.to_dict() or {}
    return CategoryOut(
        id=snap.id,
        name=data.get("name", ""),
        description=data.get("description", ""),
        parent_id=data.get("parent_id"),
        cover_image=data.get("cover_image"),
    )

@admin_router.put("/{category_id}", response_model=CategoryOut, summary="Update Category")
async def update_category(
    category_id: str,
    name: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    parent_id: Optional[str] = Form(None),
    cover_image: Optional[UploadFile] = File(None, description="Yeni kapak (opsiyonel)"),
):
    """
    Var olan kategoriyi alan bazlı günceller.
    - multipart/form-data (Form + File)
    - cover_image gönderilirse Firebase Storage'a yüklenir.
    """
    doc_ref = db.collection("categories").document(category_id)
    snap = doc_ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Category not found")

    update_data = {}
    if name is not None:
        update_data["name"] = name.strip()
    if description is not None:
        update_data["description"] = description.strip()
    if parent_id is not None:
        update_data["parent_id"] = parent_id or None

    if cover_image is not None:
        filename = cover_image.filename or f"{uuid4()}.jpg"
        blob = bucket.blob(f"categories/{category_id}/{filename}")
        try:
            blob.upload_from_file(cover_image.file, content_type=cover_image.content_type)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Image upload failed: {e}")
        try:
            blob.make_public()
            image_url = blob.public_url
        except Exception:
            image_url = blob.generate_signed_url(expiration=3600 * 24 * 365 * 10)
        update_data["cover_image"] = image_url

    if update_data:
        doc_ref.update(update_data)

    data = doc_ref.get().to_dict() or {}
    return CategoryOut(
        id=category_id,
        name=data.get("name", ""),
        description=data.get("description", ""),
        parent_id=data.get("parent_id"),
        cover_image=data.get("cover_image"),
    )

@admin_router.delete("/{category_id}", summary="Delete Category")
def delete_category(category_id: str, hard: bool = False):
    """
    Soft delete (default) veya hard delete.
    """
    doc_ref = db.collection("categories").document(category_id)
    snap = doc_ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Category not found")

    if hard:
        doc_ref.delete()
        return {"detail": "Category permanently deleted"}
    else:
        doc_ref.update({"is_deleted": True})
        return {"detail": "Category deleted"}



@router.get("/{category_id}", response_model=CategoryOut, response_model_exclude_none=True, summary="Get Category")
def get_category(category_id: str):
    """
    Tek bir kategoriyi döndürür. is_deleted=True ise 404 verir.
    """
    ref = db.collection("categories").document(category_id)
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Category not found")

    data = snap.to_dict() or {}
    if data.get("is_deleted"):
        # Soft silinmiş kayıtları public uçta göstermiyoruz
        raise HTTPException(status_code=404, detail="Category not found")

    return CategoryOut(
        id=snap.id,
        name=data.get("name", ""),
        description=data.get("description", ""),
        parent_id=data.get("parent_id"),
        cover_image=data.get("cover_image"),
    )


@router.get("", response_model=List[CategoryOut], response_model_exclude_none=True, summary="List Categories")
def list_categories_no_slash(response: Response):
    """List categories endpoint without trailing slash."""
    return _list_categories_impl(response)


@router.get("/", response_model=List[CategoryOut], response_model_exclude_none=True, summary="List Categories")
def list_categories_with_slash(response: Response):
    """List categories endpoint with trailing slash."""
    return _list_categories_impl(response)