"""
app/schemas/product.py - Pydantic models for Product.
"""
from pydantic import BaseModel, Field
from typing import List, Optional
from fastapi import Form

class ProductBase(BaseModel):
    """Common product fields for creation/update."""
    title: str = Field(..., description="Product title/name")
    description: str = Field('', description="Detailed description of the product")
    price: float = Field(..., ge=0, description="Price of the product")
    stock: int = Field(..., ge=0, description="Quantity in stock")
    category_id: str = Field(..., description="Category ID this product belongs to")
    is_upcoming: bool = Field(False, description="If true, product is coming soon (not purchasable)")


from pydantic import BaseModel
from typing import Optional
from fastapi import Form

class ProductCreate(BaseModel):
    name: str
    description: Optional[str] = ""
    price: float
    stock: int
    is_upcoming: bool = False
    category_name: str

    @classmethod
    def as_form(
        cls,
        name: str = Form(...),
        description: Optional[str] = Form(""),
        price: float = Form(...),
        stock: int = Form(...),
        is_upcoming: bool = Form(False),
        category_name: str = Form(...),
    ):
        return cls(
            name=name,
            description=description,
            price=price,
            stock=stock,
            is_upcoming=is_upcoming,
            category_name=category_name,
        )




class ProductUpdate(BaseModel):
    """Schema for updating product fields (admin)."""
    title: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    stock: Optional[int] = None
    category_id: Optional[str] = None
    is_upcoming: Optional[bool] = None
    # Not handling image updates here, might be separate endpoint or form in create.

class ProductOut(BaseModel):
    id: str
    title: str
    description: Optional[str] = ""
    price: float
    final_price: float
    stock: int
    is_upcoming: bool
    category_name: str
    images: List[str] = []

    class Config:
        orm_mode = True

