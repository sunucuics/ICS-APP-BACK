"""
app/schemas/user.py - Pydantic models for User and Address schemas.
Defines input/output structures for user-related data.
"""
from pydantic import BaseModel, EmailStr, Field
from typing import List, Optional


class Address(BaseModel):
    """Schema for a user's address."""
    id: Optional[str] = Field(None, description="Unique identifier for the address")
    label: Optional[str] = Field(None, description="Label for the address (e.g., 'Home', 'Office')")
    name: Optional[str] = Field(None, description="Recipient name for deliveries")
    address: str = Field(..., description="Full street address")
    city: str = Field(..., description="City of the address")
    country: str = Field(..., description="Country of the address")
    zipCode: str = Field(..., description="Postal code or ZIP code")
    phone: Optional[str] = Field(None, description="Contact phone number for this address")


class UserBase(BaseModel):
    """Base user schema with common fields."""
    name: Optional[str] = Field(None, description="Full name of the user")
    phone: Optional[str] = Field(None, description="Phone number")
    # No password field here because password is handled via Firebase, not stored in our DB.


class UserCreate(UserBase):
    """Schema for user registration (manual sign-up)."""
    email: EmailStr = Field(..., description="Email address (will be username for login)")
    password: str = Field(..., min_length=6, description="Password for the new account")
    # Address can be collected after registration in a separate step, so not included here.


class UserProfile(UserBase):
    """Schema for user profile output."""
    id: str = Field(..., description="User unique ID (UID from Firebase)")
    email: EmailStr = Field(..., description="Email address of the user")
    role: str = Field(..., description="Role of the user (guest, customer, admin)")
    addresses: List[Address] = Field(default_factory=list, description="List of saved addresses")

    class Config:
        orm_mode = True
        schema_extra = {
            "example": {
                "id": "12345UID",
                "name": "Alice Example",
                "email": "alice@example.com",
                "phone": "+1234567890",
                "role": "customer",
                "addresses": [
                    {
                        "id": "addr1",
                        "label": "Home",
                        "name": "Alice Example",
                        "address": "123 Main St, Apt 4",
                        "city": "Istanbul",
                        "country": "Turkey",
                        "zipCode": "34000",
                        "phone": "+1234567890"
                    }
                ]
            }
        }


class AddressCreate(BaseModel):
    """Schema for creating a new address."""
    label: Optional[str] = Field(None, description="Label for the address")
    name: Optional[str] = Field(None, description="Contact name (defaults to user name if not provided)")
    address: str = Field(..., description="Street address")
    city: str = Field(..., description="City")
    country: str = Field(..., description="Country")
    zipCode: str = Field(..., description="Postal code")
    phone: Optional[str] = Field(None, description="Contact phone for this address")


class AddressUpdate(BaseModel):
    """Schema for updating an existing address."""
    label: Optional[str] = None
    name: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    zipCode: Optional[str] = None
    phone: Optional[str] = None
