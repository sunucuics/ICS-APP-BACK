"""
# `app/schemas/user.py` — Kullanıcı ve Adres Şema Dokümantasyonu

## Genel Bilgi
Bu dosya, kullanıcı profili, adres yönetimi ve kimlik doğrulama işlemleri için kullanılan Pydantic veri modellerini tanımlar.
Frontend, formlar ve API veri alışverişi için bu alan adlarını ve veri tiplerini esas almalıdır.

---

## Ortak Tipler
- **NameStr**: En az 1 karakter, boşluklar kırpılmış.
- **PhoneStr**: `555 123 4567` formatında, regex ile doğrulanır.

---

## Adres Şemaları

### `AddressBase`
Temel adres yapısı.
| Alan         | Tip     | Zorunlu | Açıklama |
|--------------|---------|---------|----------|
| label        | `str` / `null` | ✖ | Adres etiketi |
| name         | `str` / `null` | ✖ | İletişim adı (varsayılan kullanıcı adı) |
| city         | `str`   | ✔       | Şehir |
| zipCode      | `str`   | ✔       | Posta kodu |
| district     | `str`   | ✔       | İlçe |
| neighborhood | `str` / `null` | ✖ | Mahalle |
| street       | `str` / `null` | ✖ | Sokak |
| buildingNo   | `str` / `null` | ✖ | Bina numarası |
| floor        | `str` / `null` | ✖ | Kat |
| apartment    | `str` / `null` | ✖ | Daire |
| note         | `str` / `null` | ✖ | Ek not / teslimat notu |

---

### `AddressCreate`
Yeni adres oluşturma için (Form-Data desteği ile).
- `as_form` metodu sayesinde form alanları doğrudan bu şemaya dönüştürülür.

---

### `AddressUpdate`
Adres güncelleme için tüm alanlar opsiyoneldir.

---

### `AddressOut`
API cevaplarında dönen adres yapısı (`id` eklenmiş).

---

## Kullanıcı Şemaları

### `UserBase`
Ortak kullanıcı alanları.
| Alan  | Tip     | Açıklama |
|-------|---------|----------|
| name  | `str` / `null` | Ad Soyad |
| phone | `str` / `null` | Telefon |

---

### `UserCreate`
Kullanıcı kayıt verisi.
| Alan     | Tip     | Zorunlu |
|----------|---------|---------|
| name     | `NameStr` | ✔ |
| phone    | `PhoneStr`| ✔ |
| email    | `EmailStr`| ✔ |
| password | `str` (min 6 karakter) | ✔ |

---

### `UserProfile`
Kullanıcı profil çıktısı.
| Alan      | Tip       |
|-----------|-----------|
| id        | `str`     |
| name      | `str` / `null` |
| email     | `EmailStr`|
| phone     | `str` / `null` |
| role      | `str`     |
| addresses | `list[AddressBase]` |

---

## Giriş / Çıkış Şemaları

### `LoginRequest`
Giriş isteği.
| Alan     | Tip     |
|----------|---------|
| email    | `EmailStr` |
| password | `str` (min 6 karakter) |

---

### `LoginResponse`
Başarılı girişte dönen token paketi.
| Alan          | Tip   |
|---------------|-------|
| id_token      | `str` |
| refresh_token | `str` |
| expires_in    | `int` |
| user_id       | `str` |

"""
from pydantic import BaseModel, EmailStr, constr, Field
from typing import Any, Dict, List, Optional , Annotated
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


class ProfileUpdateRequest(BaseModel):
    """Kullanıcı profil güncelleme isteği. Tüm alanlar opsiyonel, ancak en az bir alan gönderilmelidir."""
    name: Optional[str] = Field(None, description="Ad Soyad (trim edilir)")
    phone: Optional[str] = Field(None, description="Telefon (555 123 4567 formatında)")
    email: Optional[EmailStr] = Field(None, description="E-posta (lowercase edilir)")

    class Config:
        json_schema_extra = {
            "example": {
                "name": "Efehan Hüsrevoğlu",
                "phone": "555 123 4567",
                "email": "newmail@example.com"
            }
        }


class UserProfile(UserBase):
    """Schema for user profile output."""
    id: str = Field(..., description="User unique ID (UID from Firebase)")
    email: EmailStr = Field(..., description="Email address of the user")
    role: str = Field(..., description="Role of the user (guest, customer, admin)")
    addresses: List[AddressBase] = Field(default_factory=list, description="List of saved addresses")

    class Config:
        from_attributes = True
        json_schema_extra = {
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

class RegisterResponse(BaseModel):
    # Kayıttan sonra ekranda göstermek için profil
    user_id: str
    user: "UserProfile"  # ileriye referans, alt satırdaki if TYPE_CHECKING düzenin varsa kaldırabilirsin
    # Firebase tokenları
    id_token: str
    refresh_token: str
    expires_in: int