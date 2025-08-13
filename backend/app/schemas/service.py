"""
app/schemas/service.py - Pydantic models for Service (flat collection, no category).
"""
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

class ServiceBase(BaseModel):
    """Common fields for service creation/update."""
    title: str = Field(..., description="Service title/name")
    description: str = Field('', description="Description of the service")
    is_upcoming: bool = Field(False, description="If true, service is marked as coming soon")

class ServiceUpdate(BaseModel):
    """Schema for updating a service (admin)."""
    title: Optional[str] = None
    description: Optional[str] = None
    is_upcoming: Optional[bool] = None
    image: Optional[str] = None  # URL (opsiyonel)

class ServiceOut(BaseModel):
    """Schema for service info output to clients (no category fields)."""
    id: str
    title: str
    description: str
    image: Optional[str] = None  # URL to service image
    is_upcoming: bool
    is_deleted: bool
    created_at: Optional[datetime] = None  # Firestore timestamp okununca datetime gelir
    kind: Optional[str] = "service"

    class Config:
        orm_mode = True

class ServiceCreate(BaseModel):
    title: str
    description: str = ""
    is_upcoming: bool = False
