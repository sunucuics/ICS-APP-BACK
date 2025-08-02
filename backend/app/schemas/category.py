"""
app/schemas/category.py - Pydantic models for Category.
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field


class CategoryBase(BaseModel):
    """Common fields for category creation/update."""
    name: str = Field(..., description="Name of the category")
    description: str = Field("", description="Description of this category")
    parent_id: str = Field("", description="Optional parent category ID")
    is_upcoming: bool = False


class CategoryCreate(CategoryBase):
    type: Literal["product", "service"]


class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    parent_id: Optional[str] = None
    is_upcoming: Optional[bool] = None


class CategoryOut(CategoryBase):
    id: str
    type: Literal["product", "service"]

    model_config = {"from_attributes": True}
