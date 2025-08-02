"""
app/schemas/category.py - Pydantic models for Category.
"""
from pydantic import BaseModel, Field


class CategoryBase(BaseModel):
    """Common fields for category creation/updation."""
    name: str = Field(..., description="Name of the category")
    description: str = Field('', description="Description of this category")
    parent_id: str = Field('', description="Optional parent category ID (for subcategories)")
    is_upcoming: bool = Field(False, description="If true, category is marked as 'coming soon'")


class CategoryCreate(CategoryBase):
    type: str = Field(..., regex='^(product|service)$', description="Type of category: 'product' or 'service'")


class CategoryUpdate(BaseModel):
    """Fields allowed to update in a category."""
    name: str = Field(None, description="New name of the category")
    description: str = Field(None, description="New description")
    parent_id: str = Field(None, description="Change parent category")
    is_upcoming: bool = Field(None, description="Update upcoming status")


class CategoryOut(CategoryBase):
    id: str = Field(..., description="Category ID")
    type: str = Field(..., description="Type of category")

    class Config:
        orm_mode = True
