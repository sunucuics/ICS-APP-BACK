"""
app/schemas/category.py - Pydantic models for Category.
"""
from typing import Literal, Optional , Annotated
from enum import Enum
from fastapi import Form

from pydantic import BaseModel, Field

class CategoryType(str, Enum):
    product  = "product"
    service  = "service"

class CategoryBase(BaseModel):
    """Common fields for category creation/update."""
    name: str = Field(..., description="Name of the category")
    description: str = Field("", description="Description of this category")
    parent_id: str = Field("", description="Optional parent category ID")
    is_upcoming: bool = False


# ---------- input ----------
class CategoryCreate(BaseModel):
    """
    Admin ⇒ Yeni kategori oluşturma girdisi.
    ▸ parent_id yalnızca alt-kategori gerekiyorsa gönderilir.
    """
    name: str = Field(..., description="Kategori adı")
    type: Literal["product", "service"] = Field(..., description="Kategori tipi")
    description: str = Field("", description="Açıklama (opsiyonel)")
    is_upcoming: bool = Field(False, description="Yakında mı? (default False)")
    parent_id: Optional[str] = Field(
        None, description="Üst kategori ID (alt-kategori için)"
    )

    # Form-Data desteği
    @classmethod
    def as_form(
        cls,
        name: str = Form(...),
        type: str = Form(..., regex="^(product|service)$"),
        description: str = Form(""),
        is_upcoming: bool = Form(False),
        parent_id: Optional[str] = Form(None),
    ):
        return cls(
            name=name,
            type=type,
            description=description,
            is_upcoming=is_upcoming,
            parent_id=parent_id,
        )

# ---------- output ----------
class CategoryRead(CategoryCreate):
    id: str


class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    parent_id: Optional[str] = None
    is_upcoming: Optional[bool] = None


class CategoryOut(CategoryBase):
    id: str
    type: Literal["product", "service"]

    model_config = {"from_attributes": True}

