"""
# `app/schemas/appointment.py` — Appointment (Randevu) Şema Dokümantasyonu

## Genel Bilgi
Bu dosya, randevu oluşturma, güncelleme ve listeleme işlemleri için kullanılan Pydantic veri modellerini tanımlar.
Frontend tarafı, API ile veri alışverişinde bu alan adlarını ve tiplerini esas almalıdır.

---

## Kullanıcı Tarafı Şemaları

### `AppointmentRequest`
Kullanıcının randevu talebi oluşturması için gerekli alanlar.
| Alan        | Tip       | Zorunlu | Açıklama |
|-------------|-----------|---------|----------|
| service_id  | `str`     | ✔       | Randevu alınacak hizmetin ID'si |
| start       | `datetime`| ✔       | Randevu başlangıç tarihi ve saati |

---

## Admin Tarafı Şemaları

### `AppointmentAdminCreate`
Admin panelinden manuel randevu oluşturmak veya saat bloklamak için.
| Alan        | Tip               | Zorunlu | Açıklama |
|-------------|-------------------|---------|----------|
| service_id  | `str`             | ✔       | Hizmet ID'si |
| user_id     | `str` / `null`    | ✖       | Belirli kullanıcı için rezervasyon yapılacaksa kullanıcı ID'si; boş bırakılırsa saat bloklanır |
| start       | `datetime`        | ✔       | Başlangıç zamanı |
| end         | `datetime` / `null`| ✖      | Bitiş zamanı; boşsa backend varsayılan süre ekler |

---

### `AppointmentUpdate`
Randevu durumunu güncellemek için.
| Alan   | Tip                                   | Zorunlu | Açıklama |
|--------|---------------------------------------|---------|----------|
| status | `"approved"` \| `"cancelled"`         | ✔       | Yeni durum |

---

## Enum

### `AppointmentStatus`
| Değer      | Açıklama     |
|------------|--------------|
| `pending`  | Beklemede    |
| `approved` | Onaylandı    |
| `cancelled`| İptal edildi |

---

## Çıkış Şemaları (Response)

### `AppointmentOut`
Kullanıcı ve admin tarafında randevu yanıtı.
| Alan       | Tip                         | Açıklama |
|------------|-----------------------------|----------|
| id         | `str`                       | Randevu ID'si |
| service_id | `str`                       | Hizmet ID'si |
| user_id    | `str` / `null`              | Kullanıcı ID'si |
| start      | `datetime`                  | Başlangıç |
| end        | `datetime`                  | Bitiş |
| status     | `"pending"` \| `"approved"` \| `"cancelled"` | Durum |

---

### `UserBrief`
Admin listelerinde kullanıcı özet bilgisi.
| Alan      | Tip              |
|-----------|------------------|
| id        | `str`            |
| name      | `str` / `null`   |
| phone     | `str` / `null`   |
| email     | `str` / `null`   |
| addresses | `list[dict]` / `null` |

---

### `ServiceBrief`
Admin listelerinde hizmet özet bilgisi.
| Alan  | Tip              |
|-------|------------------|
| id    | `str`            |
| title | `str` / `null`   |
| price | `float` / `null` |

---

### `AppointmentAdminOut`
Admin listelerinde detaylı randevu çıktısı.
| Alan    | Tip            |
|---------|----------------|
| id      | `str`          |
| start   | `datetime`     |
| end     | `datetime`     |
| status  | `str`          |
| user    | `UserBrief`    |
| service | `ServiceBrief` |

"""
from datetime import datetime, date
from typing import Optional, Literal, List, Dict
from enum import Enum
from pydantic import BaseModel, Field


# --------- Kullanıcı ve Admin İstek Şemaları ---------

class AppointmentRequest(BaseModel):
    """Schema for users to request an appointment (booking a service)."""
    service_id: str = Field(..., description="ID of the service to book")
    start: datetime = Field(..., description="Desired appointment start datetime")


class AppointmentAdminCreate(BaseModel):
    """Schema for admin to manually create (block) an appointment slot."""
    service_id: str = Field(..., description="Hizmet ID'si")
    user_id: Optional[str] = Field(
        None, description="User ID if booking on behalf of a user; None = block slot"
    )
    start: datetime = Field(..., description="Başlangıç zamanı")
    end: Optional[datetime] = Field(
        None, description="End time; if omitted, backend will add default duration"
    )


class AppointmentUpdate(BaseModel):
    """Schema for updating appointment status."""
    status: Literal["approved", "cancelled"] = Field(..., description="Yeni durum")


# --------- Ortak Enum / Çıkış Şemaları ---------

class AppointmentStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    cancelled = "cancelled"


class AppointmentOut(BaseModel):
    id: str
    service_id: str
    user_id: Optional[str]
    start: datetime
    end: datetime
    status: AppointmentStatus

    # Pydantic v2
    model_config = {"from_attributes": True}


class UserBrief(BaseModel):
    id: str
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    addresses: Optional[List[dict]] = None


class ServiceBrief(BaseModel):
    id: str
    title: Optional[str] = None
    price: Optional[float] = None


class AppointmentAdminOut(BaseModel):
    id: str
    start: datetime
    end: datetime
    status: str
    user: UserBrief
    service: ServiceBrief


# --------- Aylık Takvim / Müsaitlik Şemaları ---------

class ServiceAvailability(BaseModel):
    """Hizmet için ustanın müsaitlik bilgileri"""
    service_id: str
    working_hours: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="Günlere göre çalışma saatleri. Örn: {'monday': ['09:00', '18:00']}"
    )
    break_times: List[Dict[str, str]] = Field(
        default_factory=list,
        description="Mola saatleri. Örn: [{'start': '12:00', 'end': '13:00'}]"
    )
    is_available: bool = Field(default=True, description="Genel müsaitlik durumu")


class TimeSlot(BaseModel):
    """Zaman dilimi bilgisi"""
    start_time: str = Field(..., description="Başlangıç saati (HH:MM)")
    end_time: str = Field(..., description="Bitiş saati (HH:MM)")
    is_available: bool = Field(..., description="Müsait mi?")
    appointment_id: Optional[str] = Field(None, description="Eğer dolu ise randevu ID'si")


class DayAvailability(BaseModel):
    """Günlük müsaitlik bilgisi"""
    work_date: date = Field(..., description="Tarih")  # field adı 'date' ile çakışmasın
    is_working_day: bool = Field(..., description="Çalışma günü mü?")
    time_slots: List[TimeSlot] = Field(default_factory=list, description="Saat dilimleri")


class MonthlyAvailability(BaseModel):
    """Aylık müsaitlik bilgisi"""
    service_id: str = Field(..., description="Hizmet ID'si")
    year: int = Field(..., description="Yıl")
    month: int = Field(..., description="Ay (1-12)")
    days: List[DayAvailability] = Field(default_factory=list, description="Günlük müsaitlikler")


class AppointmentBookingRequest(BaseModel):
    """Randevu alma talebi"""
    service_id: str = Field(..., description="Hizmet ID'si")
    booking_date: date = Field(..., description="Randevu tarihi")  # 'date: date' çakışmasını önledik
    start_time: str = Field(..., description="Başlangıç saati (HH:MM)")
    notes: Optional[str] = Field(None, description="Ek notlar")


class AppointmentWithDetails(BaseModel):
    """Detaylı randevu bilgisi"""
    id: str
    service_id: str
    user_id: Optional[str]
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    status: AppointmentStatus
    notes: Optional[str] = None
    service: Optional[ServiceBrief] = None
    user: Optional[UserBrief] = None
