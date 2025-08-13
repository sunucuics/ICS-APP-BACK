"""
app/routers/categories.py - Routes for category retrieval (public) and management (admin).
"""
from fastapi import APIRouter, Depends, HTTPException , status, Form , Query
from typing import Optional, List , Literal
from app.config import db
from app.schemas.category import CategoryCreate, CategoryUpdate, CategoryOut , CategoryRead, CategoryType
from app.core.security import get_current_admin
from google.cloud.firestore_v1 import FieldFilter


router = APIRouter(prefix="/categories", tags=["Categories"])

@router.get("/", response_model=List[CategoryOut])
def list_categories(
    category_type: Optional[Literal["product", "service"]] = Query(None, alias="type")
):
    """
    List categories. Optionally filter by type (product|service).
    Soft-deleted categories (if any) are skipped.
    """
    col = db.collection("categories")
    q = col
    if category_type:
        q = q.where(filter=FieldFilter("type", "==", category_type))

    docs = q.stream()
    out = []
    for d in docs:
        data = d.to_dict() or {}
        if data.get("is_deleted"):
            continue
        data["id"] = d.id
        out.append(data)
    return out

# Admin sub-router for category management
admin_router = APIRouter(prefix="/categories", dependencies=[Depends(get_current_admin)])

# app/routers/categories.py  (admin_router kısmı)
@admin_router.post(
    "",
    response_model=CategoryOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create Category",
)
def create_category(
    category_in: CategoryCreate = Depends(CategoryCreate.as_form)  # <— form veya JSON
):
    """
    Yeni kategori oluşturur (yalnızca admin).
    - Gönderilmeyen isteğe bağlı alanlar Firestore dokümanına yazılmaz.
    """
    doc_ref = db.collection("categories").document()
    payload = {k: v for k, v in category_in.model_dump().items() if v not in (None, "", False)}

    # Sunucu zaman damgası eklemek isterseniz:
    # payload["created_at"] = firestore.SERVER_TIMESTAMP

    doc_ref.set(payload | {"id": doc_ref.id})
    return CategoryOut(id=doc_ref.id, **payload)


@admin_router.put("/{category_id}", response_model=CategoryOut)
def update_category(category_id: str, updates: CategoryUpdate):
    """
    Admin endpoint to update an existing category.
    """
    doc_ref = db.collection("categories").document(category_id)
    if not doc_ref.get().exists:
        raise HTTPException(status_code=404, detail="Category not found")
    update_data = {k: v for k, v in updates.dict().items() if v is not None}
    if not update_data:
        # nothing to update
        cat = doc_ref.get().to_dict()
        cat['id'] = category_id
        return cat
    doc_ref.update(update_data)
    cat = doc_ref.get().to_dict()
    cat['id'] = category_id
    return cat

@admin_router.delete("/{category_id}")
def delete_category(category_id: str, hard: bool = False):
    """
    Admin endpoint to delete a category.
    If hard=true, the category is permanently deleted. Otherwise, it's a soft delete (flag it as deleted).
    """
    doc_ref = db.collection("categories").document(category_id)
    doc = doc_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Category not found")
    if hard:
        # Delete the document permanently
        doc_ref.delete()
    else:
        # Soft delete: we update a flag (we assume categories have an 'is_deleted' flag for this purpose)
        doc_ref.update({"is_deleted": True})
    return {"detail": "Category deleted" + (" permanently" if hard else "")}
