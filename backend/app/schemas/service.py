"""
app/schemas/service.py - Pydantic models for Service.
"""
from pydantic import BaseModel, Field
from typing import Optional

class ServiceBase(BaseModel):
    """Common fields for service creation/update."""
    title: str = Field(..., description="Service title/name")
    description: str = Field('', description="Description of the service")
    category_id: str = Field(..., description="Category ID for this service")
    is_upcoming: bool = Field(False, description="If true, service is marked as coming soon")


class ServiceUpdate(BaseModel):
    """Schema for updating a service (admin)."""
    title: Optional[str] = None
    description: Optional[str] = None
    category_id: Optional[str] = None
    is_upcoming: Optional[bool] = None

class ServiceOut(BaseModel):
    """Schema for service info output to clients."""
    id: str
    title: str
    description: str
    image: Optional[str] = None  # URL to service image
    category_id: str
    is_upcoming: bool
    is_deleted: bool

    class Config:
        orm_mode = True


class ServiceCreate(BaseModel):
    title: str
    description: str = ""
    is_upcoming: bool = False
    category_name: str