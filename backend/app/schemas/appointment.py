"""
app/schemas/appointment.py - Pydantic models for Appointments.
"""
from datetime import datetime
from typing import Optional, Literal

from pydantic import BaseModel, Field


class AppointmentRequest(BaseModel):
    """Schema for users to request an appointment (booking a service)."""
    service_id: str = Field(..., description="ID of the service to book")
    start: datetime = Field(..., description="Desired appointment start datetime")


class AppointmentAdminCreate(BaseModel):
    """Schema for admin to manually create (block) an appointment slot."""
    service_id: str
    user_id: Optional[str] = Field(
        None, description="User ID if booking on behalf of a user; None = block slot"
    )
    start: datetime
    end: Optional[datetime] = Field(
        None, description="End time; if omitted, backend will add default duration"
    )


class AppointmentUpdate(BaseModel):
    """Schema for updating appointment status."""
    status: Literal["approved", "cancelled"]


class AppointmentOut(BaseModel):
    id: str
    service_id: str
    user_id: Optional[str]
    start: datetime
    end: datetime
    status: Literal["pending", "approved", "cancelled"]

    model_config = {"from_attributes": True}
