"""
app/schemas/principal.py
Roller ve Principal modeli.
"""
from typing import Literal, Optional
from pydantic import BaseModel, Field

Role = Literal["guest", "user", "admin"]

class Principal(BaseModel):
    uid: str = Field(..., description="Firebase UID")
    role: Role = Field(..., description="guest | user | admin")
    email: Optional[str] = Field(None, description="E-posta (varsa)")
    display_name: Optional[str] = Field(None, description="Görünen ad (varsa)")
