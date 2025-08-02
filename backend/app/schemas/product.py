"""
app/schemas/product.py - Pydantic models for Product.
"""
from pydantic import BaseModel, Field
from typing import List, Optional

class ProductBase(BaseModel):
    """Common product fields for creation/update."""
    title: str = Field(..., description="Product title/name")
    description: str = Field('', description="Detailed description of the product")
    price: float = Field(..., ge=0, description="Price of the product")
    stock: int = Field(..., ge=0, description="Quantity in stock")
    category_id: str = Field(..., description="Category ID this product belongs to")
    is_upcoming: bool = Field(False, description="If true, product is coming soon (not purchasable)")

class ProductCreate(ProductBase):
    """Schema for product creation (admin)."""
    # Images will be handled via file uploads, so not included as Base64 or URLs here.

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
    """Schema for product information delivered to clients."""
    id: str
    title: str
    description: str
    price: float
    final_price: float = Field(..., description="Price after any discount (or same as price if no discount)")
    stock: int
    images: List[str] = Field(default_factory=list, description="List of image URLs")
    category_id: str
    is_upcoming: bool
    is_deleted: bool

    class Config:
        orm_mode = True
        schema_extra = {
            "example": {
                "id": "prod123",
                "title": "Wireless Mouse",
                "description": "A high-precision wireless mouse",
                "price": 100.0,
                "final_price": 80.0,
                "stock": 50,
                "images": [
                    "https://storage.googleapis.com/your-bucket/products/prod123/img1.jpg"
                ],
                "category_id": "electronics",
                "is_upcoming": False,
                "is_deleted": False
            }
        }
