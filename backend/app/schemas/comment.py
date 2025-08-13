"""
app/schemas/comment.py - Pydantic models for comments/reviews.
"""
from typing import Optional, Literal
from datetime import datetime
from pydantic import BaseModel, conint, constr

# Public create: 1-5 puan, max 500 karakter; hedefi body'de seçiyoruz.
class CommentCreate(BaseModel):
    target_type: Literal["product", "service"]
    target_id: str
    rating: conint(ge=1, le=5)                    # 1..5
    content: constr(min_length=1, max_length=500) # max 500

class CommentOut(BaseModel):
    id: str
    target_type: Literal["product", "service"]   # genel: ürünler ya da servisler
    target_id: str                                # genel yorumda "__all__" yazacağız
    user_id: str
    rating: int
    content: str
    is_deleted: bool = False
    created_at: Optional[datetime] = None