"""
app/routers/categories.py - Routes for category retrieval (public) and management (admin).
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional, List
from backend.app.config import db
from backend.app.schemas.category import CategoryCreate, CategoryUpdate, CategoryOut
from backend.app.core.security import get_current_admin

router = APIRouter(prefix="/categories", tags=["Categories"])

@router.get("/", response_model=List[CategoryOut])
def list_categories(type: Optional[str] = None):
    """
    List categories. Optionally filter by type (product or service).
    Only active categories are returned (is_upcoming categories are included but flagged).
    Soft-deleted categories are not returned to customers.
    """
    col_ref = db.collection("categories")
    query = col_ref
    if type:
        query = query.where("type", "==", type)
    # Exclude any categories we might mark as deleted (if we had is_deleted for categories, which we did not explicitly store; assume deletion = removed doc)
    docs = query.stream()
    categories = []
    for doc in docs:
        data = doc.to_dict()
        data['id'] = doc.id
        # If category had an is_deleted field and it's True, skip it (assuming we would set is_deleted similarly to products).
        if data.get('is_deleted'):
            continue
        categories.append(data)
    return categories

# Admin sub-router for category management
admin_router = APIRouter(prefix="/categories", dependencies=[Depends(get_current_admin)])

@admin_router.post("/", response_model=CategoryOut)
def create_category(category: CategoryCreate):
    """
    Admin endpoint to create a new category.
    """
    doc_ref = db.collection("categories").document()
    data = category.dict()
    data['created_at'] = None  # Firestore will set server timestamp if needed
    # is_deleted field can be implicitly false if not present
    doc_ref.set(data)
    data['id'] = doc_ref.id
    return data

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
