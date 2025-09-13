"""
# `app/schemas/product.py` — Ürün Şema Dokümantasyonu

## Genel Bilgi
Bu dosya, ürün oluşturma, güncelleme ve listeleme işlemleri için kullanılan Pydantic modellerini tanımlar.
Ürünler kategoriye bağlıdır ve fotoğraf yükleme işlemleri backend tarafında yönetilir.

---

## Ortak Şema

### `ProductBase`
Tüm ürünler için ortak alanlar.
| Alan         | Tip     | Zorunlu | Açıklama |
|--------------|---------|---------|----------|
| title        | `str`   | ✔       | Ürün başlığı/ismi |
| description  | `str`   | ✖       | Ürün açıklaması |
| price        | `float` | ✔       | Fiyat (≥0) |
| stock        | `int`   | ✔       | Stok adedi (≥0) |
| category_id  | `str`   | ✔       | Kategori ID’si |
| is_upcoming  | `bool`  | ✖       | Yakında mı? (satın alınamaz) |

---

## Girdi Şemaları (Input)

### `ProductCreate`
Yeni ürün oluşturmak için.
| Alan         | Tip      | Zorunlu | Açıklama |
|--------------|----------|---------|----------|
| name         | `str`    | ✔       | Ürün adı |
| description  | `str` / `null` | ✖ | Ürün açıklaması |
| price        | `float`  | ✔       | Fiyat |
| stock        | `int`    | ✔       | Stok miktarı |
| is_upcoming  | `bool`   | ✖       | Yakında mı? |
| category_name| `str`    | ✔       | Kategori adı |

**Form-Data Kullanımı:** `as_form` metodu ile desteklenir.

---

### `ProductUpdate`
Admin panelinde ürün güncelleme için.
| Alan         | Tip      | Açıklama |
|--------------|----------|----------|
| title        | `str` / `null` | Yeni başlık |
| description  | `str` / `null` | Yeni açıklama |
| price        | `float` / `null` | Yeni fiyat |
| stock        | `int` / `null` | Yeni stok |
| category_id  | `str` / `null` | Yeni kategori ID’si |
| is_upcoming  | `bool` / `null`| Yeni yakında mı bilgisi |

---

## Çıktı Şemaları (Output)

### `ProductOut`
Ürün listeleme veya detay görüntüleme yanıtı.
| Alan         | Tip     | Açıklama |
|--------------|---------|----------|
| id           | `str`   | Ürün ID’si |
| title        | `str`   | Ürün başlığı |
| description  | `str`   | Ürün açıklaması |
| price        | `float` | Orijinal fiyat |
| final_price  | `float` | İndirim uygulanmış fiyat |
| stock        | `int`   | Stok miktarı |
| is_upcoming  | `bool`  | Yakında mı bilgisi |
| category_name| `str`   | Kategori adı |
| images       | `list[str]` | Ürün görselleri listesi |

"""
from pydantic import BaseModel, Field
from typing import List, Optional
from fastapi import Form

class ProductBase(BaseModel):
    """Common product fields for creation/update."""
    title: str = Field(..., description="Product title/name")
    description: str = Field('', description="Detailed description of the product")
    price: float = Field(..., ge=0, description="Price of the product")
    stock: int = Field(..., ge=0, description="Quantity in stock")
    category_id: str = Field(..., description="Category ID this product belongs to")
    is_upcoming: bool = Field(False, description="If true, product is coming soon (not purchasable)")



class ProductCreate(BaseModel):
    name: str = Field(..., min_length=1, description="Ürün adı")
    description: str = Field("", description="Açıklama")
    price: float = Field(..., description="Fiyat")
    stock: int = Field(..., description="Stok")
    is_upcoming: bool = Field(False, description="Yakında mı?")
    category_name: str = Field(..., description="Kategori adı (ürün kategorisi)")

    # Form-data desteği
    @classmethod
    def as_form(
        cls,
        name: str = Form(...),
        description: str = Form(""),
        price: float = Form(...),
        stock: int = Form(...),
        is_upcoming: bool = Form(False),
        category_name: str = Form(...),
    ):
        return cls(
            name=name,
            description=description,
            price=price,
            stock=stock,
            is_upcoming=is_upcoming,
            category_name=category_name,
        )


class ProductUpdate(BaseModel):
    """Schema for updating product fields (admin)."""
    title: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    stock: Optional[int] = None
    category_id: Optional[str] = None
    is_upcoming: Optional[bool] = None
    # Not handling image updates here, might be separate endpoint or form in create.

class ProductOut(BaseModel):
    id: str
    title: str
    description: Optional[str] = ""
    price: float
    final_price: float
    stock: int
    is_upcoming: bool
    category_name: str
    images: List[str] = []

    model_config = {"from_attributes": True}

