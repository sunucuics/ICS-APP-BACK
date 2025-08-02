"""
app/schemas/order.py - Pydantic models for Orders.
"""
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime

class OrderItem(BaseModel):
    product_id: str
    name: str
    qty: int
    price: float  # price per unit at purchase time
    # Note: final price per item after discount could be derived from price if needed.

class OrderCreate(BaseModel):
    """Schema for creating a new order (checkout)."""
    # We might accept card details and optionally an address ID for shipping.
    address_id: Optional[str] = Field(None, description="Address ID to use for shipping (if user has multiple addresses)")
    card_holder_name: str = Field(..., description="Name on credit card")
    card_number: str = Field(..., description="Credit card number")
    expire_month: str = Field(..., description="Card expiry month (MM)")
    expire_year: str = Field(..., description="Card expiry year (YYYY)")
    cvc: str = Field(..., description="Card CVC code")

class OrderOut(BaseModel):
    id: str
    user_id: str
    items: List[OrderItem]
    total: float
    payment_status: str
    shipping_status: str
    tracking_number: Optional[str]
    carrier_code: Optional[str]
    created_at: datetime

    class Config:
        orm_mode = True
