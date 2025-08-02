"""
app/schemas/discount.py - Pydantic models for Discounts.
"""
from datetime import datetime
from typing import Optional, Literal

from pydantic import BaseModel, Field, PositiveFloat


class DiscountCreate(BaseModel):
    target_type: Literal["product", "service", "category"] = Field(
        ...,
        description="Target entity type that the discount applies to",
    )
    target_id: str = Field(
        ..., description="ID of the target (product, service, or category)"
    )
    percent: PositiveFloat = Field(
        ..., description="Discount percentage (e.g. 20 for 20 % off)"
    )
    active: bool = True
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None


class DiscountUpdate(BaseModel):
    percent: Optional[PositiveFloat] = None
    active: Optional[bool] = None
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None


class DiscountOut(BaseModel):
    id: str
    target_type: Literal["product", "service", "category"]
    target_id: str
    percent: PositiveFloat
    active: bool
    start_at: Optional[datetime]
    end_at: Optional[datetime]

    model_config = {"from_attributes": True}
