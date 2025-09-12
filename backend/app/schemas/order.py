# app/schemas/orders.py
from __future__ import annotations

from typing import Optional, List, Literal, Any, Dict
from datetime import datetime
from pydantic import BaseModel, Field

# Sipariş durumları
OrderStatus = Literal[
    "Hazırlanıyor",
    "Sipariş Alındı",
    "Kargoya Verildi",
    "Yolda",
    "Dağıtımda",
    "Teslim Edildi",
    "İptal",
    "İade",
]

# Pydantic v1: extra alanları koru (response'ta kırpılmasın)
class _Base(BaseModel):
    class Config:
        extra = "allow"

# (Input) Sepete/checkout'a gelen minimal item
class OrderItem(_Base):
    product_id: str
    name: str
    quantity: int = 1
    price: float

# (Output) Siparişte dönen satır — zengin alanlar + ürün snapshot
class OrderItemOut(_Base):
    # Zorunlu (OrderOut uyumu)
    product_id: str
    name: str
    quantity: int = 1
    price: float

    # Alias/ekler (coerce_item bunları dolduruyor)
    title: Optional[str] = None
    unit_price: Optional[float] = None
    total: Optional[float] = None
    line_total: Optional[float] = None
    currency: Optional[str] = None
    sku: Optional[str] = None
    variant_id: Optional[str] = None
    image_url: Optional[str] = None
    options: Dict[str, Any] = Field(default_factory=dict)

    # Ürün snapshot (admin panel için hızlı gösterim)
    product: Optional[Dict[str, Any]] = None

# Adres modeli (esnek; fazladan alanları saklar)
class AddressOut(_Base):
    name: Optional[str] = None
    label: Optional[str] = None
    city: Optional[str] = None
    district: Optional[str] = None
    neighborhood: Optional[str] = None
    street: Optional[str] = None
    buildingNo: Optional[str] = None
    apartment: Optional[str] = None
    floor: Optional[str] = None
    zipCode: Optional[str] = None
    note: Optional[str] = None
    id: Optional[str] = None

# Tutar özeti
class TotalsOut(_Base):
    item_count: int
    subtotal: float
    discount: float
    shipping: float
    tax: float
    grand_total: float
    currency: str

# Kargo bilgisi
class ShipmentOut(_Base):
    provider: Optional[str] = None
    tracking_number: Optional[str] = None
    status: Optional[str] = None
    simulated: Optional[bool] = None
    log: Optional[str] = None

# (Input) sipariş oluşturma payload'ı
class OrderCreate(_Base):
    items: List[OrderItem] = Field(default_factory=list)
    note: Optional[str] = None

# (Output) sipariş cevabı — tüm detaylarla
class OrderOut(_Base):
    id: str
    user_id: str
    status: OrderStatus

    # Geriye uyumluluk: üst seviyede de dursun
    tracking_number: Optional[str] = None
    shipping_provider: Literal["Aras Kargo"] = "Aras Kargo"

    integration_code: Optional[str] = None
    address: AddressOut
    items: List[OrderItemOut] = Field(default_factory=list)

    # Yeni bloklar
    totals: Optional[TotalsOut] = None
    shipment: Optional[ShipmentOut] = None

    note: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

# Pickup isteği (opsiyonel akışlar için)
class PickupBody(_Base):
    date: Optional[str] = Field(None, description="YYYY-MM-DD (boşsa ayarlardan hesaplanır)")
    window: Optional[str] = Field(None, description="örn: 13:00-17:00")
