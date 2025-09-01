# app/schemas/category.py
from typing import Optional
from fastapi import Form
from pydantic import BaseModel, Field

class CategoryBase(BaseModel):
    """Kategori ortak alanları."""
    name: str = Field(..., min_length=1, description="Kategori adı")
    description: str = Field("", description="Açıklama (opsiyonel)")
    parent_id: Optional[str] = Field(None, description="Üst kategori ID (root için boş bırak)")

# ---------- input ----------
class CategoryCreate(BaseModel):
    """
    Admin ⇒ Yeni kategori oluşturma girdisi (yalnızca ürün kategorisi).
    Kapak görseli dosya olarak endpoint'te alınır (Pydantic'e dahil edilmez).
    """
    name: str = Field(..., min_length=1, description="Kategori adı")
    description: str = Field("", description="Açıklama (opsiyonel)")
    parent_id: Optional[str] = Field(None, description="Üst kategori ID (opsiyonel)")

    # Form-Data desteği (JSON da desteklenir)
    @classmethod
    def as_form(
        cls,
        name: str = Form(...),
        description: str = Form(""),
        parent_id: Optional[str] = Form(None),
    ):
        return cls(name=name, description=description, parent_id=parent_id)

class CategoryUpdate(BaseModel):
    """Kategori güncelleme için opsiyonel alanlar."""
    name: Optional[str] = Field(None, description="Yeni kategori adı")
    description: Optional[str] = Field(None, description="Yeni açıklama")
    parent_id: Optional[str] = Field(None, description="Yeni üst kategori ID")

# ---------- output ----------
class CategoryOut(BaseModel):
    """Listeleme/Görüntüleme çıktısı."""
    id: str
    name: str
    description: str = ""
    parent_id: Optional[str] = None
    cover_image: Optional[str] = None  # Storage URL'i
