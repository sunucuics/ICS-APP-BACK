"""
app/schemas/comment.py - Pydantic models for Comments (product/service reviews).
"""
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional

class CommentCreate(BaseModel):
    """Schema for creating a new comment/review."""
    content: str = Field(..., description="Text content of the comment")
    rating: int = Field(..., ge=1, le=5, description="Rating from 1 to 5")

class CommentOut(BaseModel):
    id: str
    target_type: str
    target_id: str
    user_id: str
    content: str
    rating: int
    created_at: datetime

    class Config:
        orm_mode = True
