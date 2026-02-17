# backend/app/schemas/pagination.py
"""
Cursor-based pagination response envelope.
Firestore offset-based pagination desteklemediği için cursor (start_after) pattern kullanılır.
Mevcut orders endpoint ile aynı yapı.
"""
from pydantic import BaseModel, Field
from typing import Generic, List, TypeVar, Optional

T = TypeVar("T")


class CursorPage(BaseModel, Generic[T]):
    """
    Cursor-based paginated response.

    - items: Bu sayfadaki öğeler
    - count: Bu sayfadaki öğe sayısı
    - next_cursor: Sonraki sayfa için ISO datetime cursor (null = son sayfa)
    - has_more: Daha fazla sayfa var mı
    """
    items: List[T]
    count: int = Field(..., description="Bu sayfadaki item sayısı")
    next_cursor: Optional[str] = Field(None, description="Sonraki sayfa için ISO datetime cursor (null = son sayfa)")
    has_more: bool = Field(..., description="Daha fazla sayfa var mı")
