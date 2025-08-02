"""
app/schemas/discount.py - Pydantic models for Discounts.
"""
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional

class DiscountCreate(BaseModel):
    target_type: str = Field(..., regex='^(product|service|category)$', description="Target type that discount applies to")
    target_id: str = Field(..., description="ID of the target (product ID, service ID or category ID)")
    percent: float = Field(..., gt=0, description="Discount percentage (e.g., 20 for 20% off)")
    active: bool = Field(True, description="Whether the discount is active")
    start_at: Optional[datetime] = Field(None, description="Optional start time for the discount")
    end_at: Optional[datetime] = Field(None, description="Optional end time for the discount")

class DiscountUpdate(BaseModel):
    percent: Optional[float] = None
    active: Optional[bool] = None
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None

class DiscountOut(BaseModel):
    id: str
    target_type: str
    target_id: str
    percent: float
    active: bool
    start_at: Optional[datetime]
    end_at: Optional[datetime]

    class Config:
        orm_mode = True
