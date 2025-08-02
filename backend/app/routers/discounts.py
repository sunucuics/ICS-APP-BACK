"""
app/routers/discounts.py - Routes for managing discounts (admin).
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import List
from app.config import db
from app.core.security import get_current_admin
from app.schemas.discount import DiscountCreate, DiscountUpdate, DiscountOut

router = APIRouter(prefix="/discounts", tags=["Discounts"], dependencies=[Depends(get_current_admin)])

@router.get("/", response_model=List[DiscountOut])
def list_discounts(active: bool = None):
    """
    List all discounts. Optionally filter by active status.
    """
    query = db.collection("discounts")
    if active is not None:
        query = query.where("active", "==", bool(active))
    docs = query.stream()
    discounts = []
    for doc in docs:
        data = doc.to_dict()
        data['id'] = doc.id
        discounts.append(data)
    return discounts

@router.post("/", response_model=DiscountOut)
def create_discount(discount: DiscountCreate):
    """
    Create a new discount entry.
    """
    disc_ref = db.collection("discounts").document()
    data = discount.dict()
    # Firestore can't store datetime directly via dict unless it's aware; could use Timestamp or keep as string
    disc_ref.set(data)
    data['id'] = disc_ref.id
    return data

@router.put("/{discount_id}", response_model=DiscountOut)
def update_discount(discount_id: str, updates: DiscountUpdate):
    """
    Update an existing discount.
    """
    disc_ref = db.collection("discounts").document(discount_id)
    if not disc_ref.get().exists:
        raise HTTPException(status_code=404, detail="Discount not found")
    update_data = {k: v for k, v in updates.dict().items() if v is not None}
    if update_data:
        disc_ref.update(update_data)
    doc = disc_ref.get().to_dict()
    doc['id'] = discount_id
    return doc

@router.delete("/{discount_id}")
def delete_discount(discount_id: str):
    """
    Delete a discount (hard delete).
    """
    disc_ref = db.collection("discounts").document(discount_id)
    if not disc_ref.get().exists:
        raise HTTPException(status_code=404, detail="Discount not found")
    disc_ref.delete()
    return {"detail": "Discount deleted"}
