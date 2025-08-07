"""
app/schemas/user.py - Pydantic models for User and Address schemas.
Defines input/output structures for user-related data.
"""
from pydantic import BaseModel, EmailStr, constr, Field
from typing import List, Optional , Annotated
from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from fastapi import Form

PHONE_REGEX = r'^\d{3}\s\d{3}\s\d{4}$'
NameStr  = Annotated[str, Field(min_length=1, strip_whitespace=True)]
PhoneStr = Annotated[str, Field(pattern=PHONE_REGEX)]

class AddressBase(BaseModel):
    # Mevcut alanlar – isimleri koruduk
    label:    Optional[str] = Field(None, description="Label for the address")
    name:     Optional[str] = Field(None, description="Contact name (defaults to user name)")
    city:     str           = Field(...,  description="City")
    zipCode:  str           = Field(...,  description="Postal code")

    # Yeni eklenen kutular
    district:     str           = Field(...,  description="District / İlçe")
    neighborhood: Optional[str] = Field(None, description="Mahalle")
    street:       Optional[str] = Field(None, description="Sokak (detay)")
    buildingNo:   Optional[str] = Field(None, description="Bina No")
    floor:        Optional[str] = Field(None, description="Kat")
    apartment:    Optional[str] = Field(None, description="Daire")
    note:         Optional[str] = Field(None, description="Ek not / teslimat notu")


class UserBase(BaseModel):
    """Base user schema with common fields."""
    name: Optional[str] = Field(None, description="Full name of the user")
    phone: Optional[str] = Field(None, description="Phone number")
    # No password field here because password is handled via Firebase, not stored in our DB.


class UserCreate(BaseModel):
    """Kullanıcı kayıt verisi."""
    name: NameStr                        = Field(..., description="Ad Soyad")
    phone: PhoneStr                      = Field(..., description="Telefon (555 123 4567)")
    email: EmailStr                      = Field(..., description="E-posta")
    password: Annotated[
        str, Field(min_length=6)
    ] = Field(..., description="Şifre (min 6 karakter)")


class UserProfile(UserBase):
    """Schema for user profile output."""
    id: str = Field(..., description="User unique ID (UID from Firebase)")
    email: EmailStr = Field(..., description="Email address of the user")
    role: str = Field(..., description="Role of the user (guest, customer, admin)")
    addresses: List[AddressBase] = Field(default_factory=list, description="List of saved addresses")

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
                        "zipCode": "34000",
                        "phone": "+1234567890"
                    }
                ]
            }
        }


class AddressCreate(BaseModel):
    """Schema for creating a new address via FORM data."""
    label:    Optional[str] = Field(None)
    name:     Optional[str] = Field(None)
    city:     str           = Field(...)
    zipCode:  str           = Field(...)

    district:     str           = Field(...)
    neighborhood: Optional[str] = Field(None)
    street:       Optional[str] = Field(None)
    buildingNo:   Optional[str] = Field(None)
    floor:        Optional[str] = Field(None)
    apartment:    Optional[str] = Field(None)
    note:         Optional[str] = Field(None)

    # 🔸 burası kritik: Form converter
    @classmethod
    def as_form(
        cls,
        label:        Optional[str] = Form(None),
        name:         Optional[str] = Form(None),
        city:         str           = Form(...),
        zipCode:      str           = Form(...),
        district:     str           = Form(...),
        neighborhood: Optional[str] = Form(None),
        street:       Optional[str] = Form(None),
        buildingNo:   Optional[str] = Form(None),
        floor:        Optional[str] = Form(None),
        apartment:    Optional[str] = Form(None),
        note:         Optional[str] = Form(None),
    ):
        return cls(
            label=label,
            name=name,
            city=city,
            zipCode=zipCode,
            district=district,
            neighborhood=neighborhood,
            street=street,
            buildingNo=buildingNo,
            floor=floor,
            apartment=apartment,
            note=note,
        )


class AddressUpdate(BaseModel):
    """Adres güncellerken – tüm alanlar opsiyonel."""
    label:        Optional[str] = None
    name:         Optional[str] = None
    city:         Optional[str] = None
    zipCode:      Optional[str] = None
    phone:        Optional[str] = None
    district:     Optional[str] = None
    neighborhood: Optional[str] = None
    street:       Optional[str] = None
    buildingNo:   Optional[str] = None
    floor:        Optional[str] = None
    apartment:    Optional[str] = None
    note:         Optional[str] = None

class AddressOut(AddressBase):
    """API cevaplarında dönen adres yapısı."""
    id: str = Field(..., description="Firestore document ID")

class LoginRequest(BaseModel):
    """İstemciden gelen giriş verisi."""
    email:  EmailStr                                  = Field(..., description="E-posta")
    password: Annotated[str, Field(min_length=6)]     = Field(..., description="Şifre (≥6 kr.)")


class LoginResponse(BaseModel):
    """Başarılı girişte dönen token paketi."""
    id_token:      str
    refresh_token: str
    expires_in:    int         # saniye
    user_id:       str