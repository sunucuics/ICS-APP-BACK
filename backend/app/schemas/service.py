"""
# `app/schemas/service.py` — Hizmet Şema Dokümantasyonu

## Genel Bilgi
Bu dosya, hizmet (service) oluşturma, güncelleme ve listeleme işlemleri için kullanılan Pydantic modellerini tanımlar.
Hizmetler kategoriye bağlı değildir, `services/{id}` şeklinde düz (flat) bir koleksiyonda tutulur.

---

## Ortak Şema

### `ServiceBase`
Tüm hizmetler için ortak alanlar.
| Alan        | Tip     | Zorunlu | Açıklama |
|-------------|---------|---------|----------|
| title       | `str`   | ✔       | Hizmet başlığı/ismi |
| description | `str`   | ✖       | Hizmet açıklaması |
| is_upcoming | `bool`  | ✖       | Yakında mı? |

---

## Girdi Şemaları (Input)

### `ServiceCreate`
Yeni hizmet oluşturmak için.
| Alan        | Tip     | Zorunlu | Açıklama |
|-------------|---------|---------|----------|
| title       | `str`   | ✔       | Hizmet başlığı |
| description | `str`   | ✖       | Açıklama |
| is_upcoming | `bool`  | ✖       | Yakında mı? |

---

### `ServiceUpdate`
Admin panelinde hizmet güncellemek için.
| Alan        | Tip     | Açıklama |
|-------------|---------|----------|
| title       | `str` / `null` | Yeni başlık |
| description | `str` / `null` | Yeni açıklama |
| is_upcoming | `bool` / `null`| Yeni yakında mı bilgisi |
| image       | `str` / `null` | Yeni görsel URL’si |

---

## Çıktı Şemaları (Output)

### `ServiceOut`
Hizmet listeleme veya detay yanıtı.
| Alan        | Tip     | Açıklama |
|-------------|---------|----------|
| id          | `str`   | Hizmet ID’si |
| title       | `str`   | Hizmet başlığı |
| description | `str`   | Açıklama |
| image       | `str` / `null` | Görsel URL’si |
| is_upcoming | `bool`  | Yakında mı bilgisi |
| is_deleted  | `bool`  | Silinmiş mi bilgisi |
| created_at  | `datetime` / `null` | Oluşturulma zamanı |
| kind        | `str` / `null` | Tür bilgisi (varsayılan `"service"`) |

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

    model_config = {"from_attributes": True}

class ServiceCreate(BaseModel):
    title: str
    description: str = ""
    is_upcoming: bool = False
