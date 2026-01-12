# backend/app/schemas/admin_notification.py
"""
Admin Notification Schemas
Müşteri randevu taleplerinde admin panele gönderilen bildirimler için şemalar
"""
from pydantic import BaseModel, Field
from typing import Optional, Literal, Dict, Any
from datetime import datetime


class AdminNotificationBase(BaseModel):
    """Admin bildirimi için temel şema"""
    title: str = Field(..., description="Bildirim başlığı")
    body: str = Field(..., description="Bildirim içeriği")
    type: Literal["appointment", "order", "comment", "system"] = Field(
        default="system", 
        description="Bildirim tipi: appointment, order, comment, system"
    )
    data: Optional[Dict[str, Any]] = Field(
        default=None, 
        description="Bildirimle ilişkili ekstra veriler (appointment_id, user_id vb.)"
    )


class AdminNotificationCreate(AdminNotificationBase):
    """Admin bildirimi oluşturma şeması"""
    pass


class AdminNotificationOut(AdminNotificationBase):
    """Admin bildirimi çıktı şeması"""
    id: str
    is_read: bool = Field(default=False, description="Okundu mu?")
    created_at: Optional[datetime] = None
    read_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class UnreadCountResponse(BaseModel):
    """Okunmamış bildirim sayısı yanıtı"""
    count: int = Field(..., description="Okunmamış bildirim sayısı")
