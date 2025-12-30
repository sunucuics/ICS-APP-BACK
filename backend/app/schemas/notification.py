# backend/app/schemas/notification.py
from pydantic import BaseModel, Field
from typing import Optional, Literal, Dict, Any, List
from datetime import datetime

class NotificationTemplateBase(BaseModel):
    name: str = Field(..., description="Template adı, admin panelde görünen başlık")
    subject: Optional[str] = Field(None, description="E-posta konusu / push title / SMS başlığı")
    body: str = Field(..., description="Mesaj içeriği (email HTML olabilir)")
    type: Literal["email", "sms", "push"] = Field(..., description="Kanal tipi")
    is_active: bool = True

class NotificationTemplateOut(NotificationTemplateBase):
    id: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

class UserNotificationOut(BaseModel):
    id: str
    user_id: str
    title: str
    body: str
    type: str = Field(default="admin_notification", description="Bildirim tipi")
    is_read: bool = Field(default=False, description="Okundu mu?")
    created_at: Optional[datetime] = None
    data: Optional[Dict[str, Any]] = Field(default=None, description="Ekstra veriler")

class NotificationSendRequest(BaseModel):
    title: str = Field(..., description="Bildirim başlığı")
    body: str = Field(..., description="Bildirim içeriği")
    template_id: Optional[str] = Field(None, description="Şablon ID (opsiyonel)")
    user_segments: Optional[List[str]] = Field(None, description="Hedef kullanıcı segmentleri")
    segments: Optional[List[str]] = Field(None, description="Hedef kullanıcı segmentleri (geriye dönük uyumluluk)")
    target_users: Optional[List[str]] = Field(None, description="Hedef kullanıcı ID'leri")
    send_immediately: bool = Field(True, description="Hemen gönder")
    scheduled_at: Optional[datetime] = Field(None, description="Zamanlanmış gönderim tarihi")
