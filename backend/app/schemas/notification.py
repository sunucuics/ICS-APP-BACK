# backend/app/schemas/notification.py
from pydantic import BaseModel, Field
from typing import Optional, Literal
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
