"""
# `app/routers/users.py` — Kullanıcı ve Adres Yönetimi Dokümantasyonu

## Genel Bilgi
Bu dosya, giriş yapmış kullanıcının profilini görüntülemesini, adres ekleme/güncelleme/silme işlemlerini ve tüm adreslerini listelemesini sağlar.
Tüm işlemler `get_current_user` bağımlılığı ile kimlik doğrulama gerektirir.

---

## Endpoint’ler

### `GET /users/me`
**Amaç:** Giriş yapmış kullanıcının profilini döndürmek.

**İşleyiş:**
1. `get_current_user` ile kullanıcı bilgisi Firestore’dan alınır.
2. Profil bilgileri döndürülür.

---

### `POST /users/me/addresses`
**Amaç:** Yeni adres eklemek.

**Parametreler (Form-Data):** `AddressCreate` şeması.
**İşleyiş:**
1. Yeni adres için `uuid4()` ile benzersiz `id` üretilir.
2. Adres bilgileri kullanıcının mevcut adres listesine eklenir.
3. Firestore’da `users/{user_id}` dokümanındaki `addresses` alanı güncellenir.
4. Eklenen adres döndürülür.

---

### `PUT /users/me/addresses/{addr_id}`
**Amaç:** Mevcut adresi güncellemek.

**Parametreler:**
- `addr_id`: Güncellenecek adresin ID’si
- `AddressUpdate` şeması (JSON)

**İşleyiş:**
1. Kullanıcının adres listesi çekilir.
2. `addr_id` eşleşen adres bulunur ve gönderilen alanlar güncellenir.
3. Güncellenmiş adres listesi Firestore’a yazılır.
4. Güncel profil döndürülür.

---

### `DELETE /users/me/addresses/{addr_id}`
**Amaç:** Adres silmek.

**Parametreler:**
- `addr_id`: Silinecek adresin ID’si

**İşleyiş:**
1. Kullanıcının adres listesi çekilir.
2. `addr_id` eşleşmeyen adresler listede tutulur.
3. Adres bulunamazsa `404` döner.
4. Güncellenmiş adres listesi Firestore’a yazılır.
5. Güncel profil döndürülür.

---

### `GET /users/me/addresses`
**Amaç:** Giriş yapmış kullanıcının tüm adreslerini listelemek.

**İşleyiş:**
1. Firestore’dan `users/{user_id}` dokümanı çekilir.
2. `addresses` alanı liste olarak döndürülür.

"""
from fastapi import APIRouter, Depends, HTTPException
from uuid import uuid4
from app.core.security import get_current_user
from app.config import db
from app.schemas.user import UserProfile, AddressCreate, AddressUpdate , AddressOut

router = APIRouter(prefix="/users", tags=["Users"])

@router.get("/me", response_model=UserProfile)
def get_my_profile(current_user: dict = Depends(get_current_user)):
    """
    Get the profile of the currently authenticated user.
    """
    # current_user is retrieved from Firestore in the security dependency
    return current_user

@router.post("/me/addresses", response_model=AddressOut)
def add_address(
    address: AddressCreate,
    current_user: dict = Depends(get_current_user),
):
    """
    Add a new address to the current user's address list and
    return the created address info.
    """
    user_id = current_user["id"]

    # Firestore için yeni belge verisi
    addr_id = str(uuid4())
    new_addr = {
        "id":         addr_id,
        "label":      address.label or "",
        "name":       address.name or current_user.get("name", ""),
        "city":       address.city,
        "district":   address.district,
        "zipCode":    address.zipCode,
        "neighborhood": address.neighborhood,
        "street":       address.street,
        "buildingNo":   address.buildingNo,
        "floor":        address.floor,
        "apartment":    address.apartment,
        "note":         address.note,
    }

    user_ref = db.collection("users").document(user_id)
    snap = user_ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="User profile not found")

    # adresi listeye ekle
    addresses = snap.to_dict().get("addresses", [])
    addresses.append(new_addr)
    user_ref.update({"addresses": addresses})

    return AddressOut(**new_addr)

@router.post("/me/addresses/{addr_id}/choose-current", response_model=AddressOut)
def choose_current_address(addr_id: str, current_user: dict = Depends(get_current_user)):
    """
    Set an address as the current/default address for the user.
    """
    user_id = current_user['id']
    user_ref = db.collection("users").document(user_id)
    doc = user_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="User not found")
    
    profile = doc.to_dict()
    addresses = profile.get('addresses', [])
    
    # Find the address to set as current
    target_address = None
    for addr in addresses:
        if addr.get('id') == addr_id:
            target_address = addr
            break
    
    if not target_address:
        raise HTTPException(status_code=404, detail="Address not found")
    
    # Update default address field in user profile
    user_ref.update({"defaultAddressId": addr_id})
    
    return AddressOut(**target_address)

@router.put("/me/addresses/{addr_id}", response_model=UserProfile)
def update_address(addr_id: str, addr_update: AddressUpdate, current_user: dict = Depends(get_current_user)):
    """
    Update an existing address of the current user.
    Returns the updated profile.
    """
    user_id = current_user['id']
    user_ref = db.collection("users").document(user_id)
    doc = user_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="User not found")
    profile = doc.to_dict()
    addresses = profile.get('addresses', [])
    updated = False
    for addr in addresses:
        if addr.get('id') == addr_id:
            # Update provided fields
            if addr_update.label is not None: addr['label'] = addr_update.label
            if addr_update.name is not None: addr['name'] = addr_update.name
            if addr_update.city is not None: addr['city'] = addr_update.city
            if addr_update.zipCode is not None: addr['zipCode'] = addr_update.zipCode
            if addr_update.phone is not None: addr['phone'] = addr_update.phone
            updated = True
            break
    if not updated:
        raise HTTPException(status_code=404, detail="Address not found")
    user_ref.update({"addresses": addresses})
    profile['addresses'] = addresses
    profile['id'] = user_id
    return profile

@router.delete("/me/addresses/{addr_id}", response_model=UserProfile)
def delete_address(addr_id: str, current_user: dict = Depends(get_current_user)):
    """
    Delete an address from the current user's profile.
    Returns updated profile.
    """
    user_id = current_user['id']
    user_ref = db.collection("users").document(user_id)
    doc = user_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="User not found")
    profile = doc.to_dict()
    addresses = profile.get('addresses', [])
    new_addresses = [addr for addr in addresses if addr.get('id') != addr_id]
    if len(new_addresses) == len(addresses):
        # no change, address not found
        raise HTTPException(status_code=404, detail="Address not found")
    user_ref.update({"addresses": new_addresses})
    profile['addresses'] = new_addresses
    profile['id'] = user_id
    return profile

@router.get("/me/addresses", response_model=list[AddressOut])
def list_addresses(current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    snap = db.collection("users").document(user_id).get()
    if not snap.exists:
        raise HTTPException(404, "User profile not found")

    addresses = snap.to_dict().get("addresses", [])
    return [AddressOut(**addr) for addr in addresses]


@router.get("/me/addresses/current", response_model=AddressOut)
def get_current_address(current_user: dict = Depends(get_current_user)):
    """
    Return the user's currently selected (default) address.
    Looks up `defaultAddressId` on the user document and returns that address.
    """
    user_id = current_user["id"]
    user_ref = db.collection("users").document(user_id)
    doc = user_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="User not found")

    data = doc.to_dict()
    default_id = data.get("defaultAddressId")
    if not default_id:
        raise HTTPException(status_code=404, detail="No default address set")

    addresses = data.get("addresses", [])
    current = next((a for a in addresses if a.get("id") == default_id), None)
    if not current:
        # default id is stale or address was deleted
        raise HTTPException(status_code=404, detail="Default address not found")

    return AddressOut(**current)
