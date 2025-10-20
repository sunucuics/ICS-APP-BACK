from __future__ import annotations
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, validator

class OrderItem(BaseModel):
    product_id: str
    name: str
    quantity: int = Field(gt=0)
    price: float = Field(ge=0)
    meta: Optional[Dict[str, Any]] = None  # isteğe bağlı

    # İstemciden 0 gelirse dahi kullanıcıyı yormayalım; backend 1'e düzeltir.
    @validator("quantity", pre=True)
    def _min_one(cls, v):
        try:
            v = int(v)
        except Exception:
            raise ValueError("quantity sayı olmalı")
        return 1 if v < 1 else v

class OrderCreate(BaseModel):
    # İSTEMCİ GÖNDERMESE DE OLUR → sepetten okunur
    items: Optional[List[OrderItem]] = None
    note: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None

class DispatchResult(BaseModel):
    tracking_number: Optional[str] = None
    cargo_key: Optional[str] = None
    invoice_key: Optional[str] = None
    waybill_no: Optional[str] = None
    message: str
    raw_xml: Optional[str] = None
    fields: Optional[Dict[str, Any]] = None

class OrderOut(BaseModel):
    order_id: str
    checkout_id: Optional[str] = None
    user_id: str
    status: str
    tracking_number: Optional[str] = None
    provider: str = "aras"
    message: str
    clear_cart_on_success: bool
    created_at: Optional[str] = None
    shipping: Optional[DispatchResult] = None
