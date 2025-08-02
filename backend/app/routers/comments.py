"""
app/routers/comments.py - Routes for user comments (reviews) and admin moderation.
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import List
from app.core.security import get_current_user, get_current_admin
from app.config import db
from app.schemas.comment import CommentCreate, CommentOut

router = APIRouter(tags=["Comments"])

@router.post("/products/{product_id}/comments", response_model=CommentOut)
def add_product_comment(product_id: str, comment: CommentCreate, current_user: dict = Depends(get_current_user)):
    """
    Add a comment/review for a product by a user. User must have purchased the product.
    """
    user_id = current_user['id']
    # Simple check: user must have an order containing this product_id
    orders = db.collection("orders").where("user_id", "==", user_id).stream()
    purchased = False
    for order in orders:
        order_data = order.to_dict()
        for item in order_data.get('items', []):
            if item.get('product_id') == product_id:
                purchased = True
                break
        if purchased:
            break
    if not purchased:
        raise HTTPException(status_code=400, detail="Cannot review a product not purchased")
    # Profanity filter
    profanity_doc = db.collection("settings").document("profanity").get()
    blocked_words = []
    if profanity_doc.exists:
        blocked_words = profanity_doc.to_dict().get('blocked_words', [])
    for bad_word in blocked_words:
        if bad_word.lower() in comment.content.lower():
            raise HTTPException(status_code=400, detail="Comment contains inappropriate language")
    # Create comment
    comm_ref = db.collection("comments").document()
    comm_data = {
        "target_type": "product",
        "target_id": product_id,
        "user_id": user_id,
        "rating": comment.rating,
        "content": comment.content,
        "is_deleted": False,
        "created_at": None
    }
    comm_ref.set(comm_data)
    comm_data['id'] = comm_ref.id
    return comm_data

@router.post("/services/{service_id}/comments", response_model=CommentOut)
def add_service_comment(service_id: str, comment: CommentCreate, current_user: dict = Depends(get_current_user)):
    """
    Add a comment/review for a service by a user. User must have completed an appointment for the service.
    """
    user_id = current_user['id']
    # Check if user had an approved (and presumably past) appointment for this service
    appts = db.collection("appointments").where("user_id", "==", user_id).where("service_id", "==", service_id).where("status", "==", "approved").stream()
    had_appointment = False
    for appt in appts:
        had_appointment = True
        break
    if not had_appointment:
        raise HTTPException(status_code=400, detail="Cannot review a service not utilized")
    # Profanity filter (same as above)
    profanity_doc = db.collection("settings").document("profanity").get()
    blocked_words = []
    if profanity_doc.exists:
        blocked_words = profanity_doc.to_dict().get('blocked_words', [])
    for bad_word in blocked_words:
        if bad_word.lower() in comment.content.lower():
            raise HTTPException(status_code=400, detail="Comment contains inappropriate language")
    comm_ref = db.collection("comments").document()
    comm_data = {
        "target_type": "service",
        "target_id": service_id,
        "user_id": user_id,
        "rating": comment.rating,
        "content": comment.content,
        "is_deleted": False,
        "created_at": None
    }
    comm_ref.set(comm_data)
    comm_data['id'] = comm_ref.id
    return comm_data

# Admin moderation endpoints
admin_router = APIRouter(prefix="/comments", dependencies=[Depends(get_current_admin)])

@admin_router.get("/", response_model=List[CommentOut])
def list_comments(target_type: str = None, target_id: str = None):
    """
    Admin endpoint to list comments. Can filter by target_type and/or target_id.
    """
    query = db.collection("comments")
    if target_type:
        query = query.where("target_type", "==", target_type)
    if target_id:
        query = query.where("target_id", "==", target_id)
    docs = query.stream()
    comments = []
    for doc in docs:
        data = doc.to_dict()
        data['id'] = doc.id
        comments.append(data)
    return comments

@admin_router.delete("/{comment_id}")
def delete_comment(comment_id: str, hard: bool = False):
    """
    Admin endpoint to delete a comment.
    Soft delete by default (mark is_deleted), or hard delete if specified.
    """
    comm_ref = db.collection("comments").document(comment_id)
    doc = comm_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Comment not found")
    if hard:
        comm_ref.delete()
    else:
        comm_ref.update({"is_deleted": True})
    return {"detail": f"Comment {'hard ' if hard else ''}deleted"}
