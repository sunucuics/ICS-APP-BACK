# app/schemas/featured.py
from __future__ import annotations
from typing import List, Optional, Literal
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime

FeaturedKind = Literal["products", "services"]

class FeaturedItemOut(BaseModel):
    id: str = Field(..., description="Öne çıkarılan ürün/hizmet ID'si")
    created_by: Optional[str] = Field(None, description="Kaydı oluşturan admin UID")
    created_at: Optional[datetime] = Field(None, description="Oluşturulma zamanı (UTC)")

class FeaturedListOut(BaseModel):
    items: List[FeaturedItemOut]

class FeaturedExpandedDoc(BaseModel):
    id: str = Field(..., description="Kaynağın ID'si (service/product dokümanı)")
    model_config = ConfigDict(extra="allow")  # ürün/hizmet dokümanındaki tüm alanlara izin
