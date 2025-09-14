# app/schemas/comment.py
from __future__ import annotations
from datetime import datetime
from typing import Optional, Literal , List
from pydantic import BaseModel, Field

TargetType = Literal["product", "service"]

class CommentOut(BaseModel):
    id: str
    target_type: TargetType
    target_id: str
    user_id: str
    user_name: Optional[str] = None  # <-- eklendi
    content: str = Field(min_length=1, max_length=500)
    rating: int = Field(ge=1, le=5)
    is_deleted: bool = False
    is_hidden: bool = False
    created_at: Optional[datetime] = None

class ProfanityIn(BaseModel):
    blocked_words: List[str] = Field(default_factory=list, description="Küfür listesi (string)")

class ProfanityWordsIn(BaseModel):
    words: List[str] = Field(default_factory=list, description="Eklenecek/Silinecek kelimeler")