"""
app/schemas/appointment.py - Pydantic models for Appointments.
"""
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional

class AppointmentRequest(BaseModel):
    """Schema for users to request an appointment (booking a service)."""
    service_id: str = Field(..., description="ID of the service to book")
    start: datetime = Field(..., description="Desired appointment start datetime")

class AppointmentAdminCreate(BaseModel):
    """Schema for admin to manually create (block) an appointment slot."""
    service_id: str = Field(..., description="Service ID for the appointment")
    user_id: Optional[str] = Field(None, description="User ID if booking on behalf of a user (or leave null to block)")
    start: datetime = Field(..., description="Appointment start time")
    end: Optional[datetime] = Field(None, description="Appointment end time (if not provided, will assume a default duration)")

class AppointmentUpdate(BaseModel):
    """Schema for updating appointment status (admin approves or cancels)."""
    status: str = Field(..., regex='^(approved|cancelled)$', description="New status of the appointment")

class AppointmentOut(BaseModel):
    """Schema for appointment info output."""
    id: str
    service_id: str
    user_id: Optional[str]
    start: datetime
    end: datetime
    status: str

    class Config:
        orm_mode = True
