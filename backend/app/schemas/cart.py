"""
app/schemas/cart.py - Pydantic models for Cart.
"""
from pydantic import BaseModel, Field
from typing import List

class CartItem(BaseModel):
    product_id: str = Field(..., description="ID of the product")
    name: str = Field(..., description="Name of the product")
    price: float = Field(..., description="Price per unit at the time of adding to cart")
    qty: int = Field(..., gt=0, description="Quantity of the product in the cart")

class Cart(BaseModel):
    user_id: str = Field(..., description="ID of the user who owns this cart")
    items: List[CartItem] = Field(default_factory=list, description="List of cart items")

    class Config:
        orm_mode = True
