"""
app/routers/users.py - Routes for user profile and address management.
Includes endpoints to get current user profile, add/update/delete addresses, etc.
"""
from fastapi import APIRouter, Depends, HTTPException
from uuid import uuid4
from app.core.security import get_current_user
from app.config import db
from app.schemas.user import UserProfile, AddressCreate, AddressUpdate

router = APIRouter(prefix="/users", tags=["Users"])

@router.get("/me", response_model=UserProfile)
def get_my_profile(current_user: dict = Depends(get_current_user)):
    """
    Get the profile of the currently authenticated user.
    """
    # current_user is retrieved from Firestore in the security dependency
    return current_user

@router.post("/me/addresses", response_model=UserProfile)
def add_address(address: AddressCreate, current_user: dict = Depends(get_current_user)):
    """
    Add a new address to the current user's address list.
    Returns the updated user profile.
    """
    user_id = current_user['id']
    # Generate a unique ID for the new address
    addr_id = str(uuid4())
    new_addr = {
        "id": addr_id,
        "label": address.label or "",
        "name": address.name or current_user.get('name', ""),
        "address": address.address,
        "city": address.city,
        "country": address.country,
        "zipCode": address.zipCode,
        "phone": address.phone or current_user.get('phone', "")
    }
    user_ref = db.collection("users").document(user_id)
    # Atomically add the address to the addresses array (could use arrayUnion, but merging might be simpler to avoid dup)
    user_doc = user_ref.get()
    if not user_doc.exists:
        raise HTTPException(status_code=404, detail="User profile not found")
    profile = user_doc.to_dict()
    addresses = profile.get('addresses', [])
    addresses.append(new_addr)
    user_ref.update({"addresses": addresses})
    profile['addresses'] = addresses
    profile['id'] = user_id
    return profile

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
            if addr_update.address is not None: addr['address'] = addr_update.address
            if addr_update.city is not None: addr['city'] = addr_update.city
            if addr_update.country is not None: addr['country'] = addr_update.country
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
