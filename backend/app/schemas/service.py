# backend/app/schemas/service.py
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime


class ServiceBase(BaseModel):
    """
    Ortak alanlar.
    """
    title: str = Field(..., description="Service title/name")
    description: str = Field("", description="Service description")
    is_upcoming: bool = Field(False, description="If true, service is marked as coming soon")


class ServiceCreate(ServiceBase):
    """
    Create input (JSON kullanımında).
    Not: Biz create endpoint’inde multipart/form-data (Form + File) kullanıyoruz.
    Bu şema daha çok dokümantasyon / ileride JSON create ihtiyacı için tutulur.
    """
    pass


class ServiceUpdate(BaseModel):
    """
    Update input (JSON kullanımında).
    Not: Biz update endpoint’inde multipart/form-data (Form + File) kullanıyoruz.
    """
    title: Optional[str] = Field(None, description="New title")
    description: Optional[str] = Field(None, description="New description")
    is_upcoming: Optional[bool] = Field(None, description="New upcoming flag")


class ServiceOut(BaseModel):
    """
    API output.
    """
    id: str = Field(..., description="Service ID")
    title: str = Field(..., description="Service title/name")
    description: str = Field("", description="Service description")
    is_upcoming: bool = Field(False, description="Coming soon?")
    is_deleted: bool = Field(False, description="Soft deleted?")
    created_at: Optional[datetime] = Field(None, description="Created timestamp")
    kind: Optional[str] = Field("service", description="Document kind")

    # Yeni format: 0-3 image URL
    images: List[str] = Field(default_factory=list, description="Service images (0-3)")

    # Geri uyum: ilk görsel
    image: Optional[str] = Field(None, description="First image URL (backward compatibility)")
