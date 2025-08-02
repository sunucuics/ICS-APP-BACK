"""
app/routers/services.py - Routes for service listing (public) and management (admin).
"""
from fastapi import APIRouter, Depends, HTTPException, File, UploadFile, Form, status
from typing import List
from uuid import uuid4
from app.config import db, bucket
from app.core.security import get_current_admin
from app.schemas.service import ServiceOut

router = APIRouter(prefix="/services", tags=["Services"])

@router.get("/", response_model=List[ServiceOut])
def list_services(category_id: str = None):
    """
    List all available services, optionally filtered by category.
    Excludes deleted services; includes upcoming services (marked accordingly).
    """
    services_ref = db.collection("services")
    query = services_ref.where("is_deleted", "==", False)
    if category_id:
        query = query.where("category_id", "==", category_id)
    docs = query.stream()
    services = []
    for doc in docs:
        data = doc.to_dict()
        if data.get('is_deleted'):
            continue
        data['id'] = doc.id
        services.append(data)
    return services

# Admin sub-router
admin_router = APIRouter(prefix="/services", dependencies=[Depends(get_current_admin)])

@admin_router.post("/", response_model=ServiceOut, status_code=status.HTTP_201_CREATED)
async def create_service(
    title: str = Form(...),
    description: str = Form(""),
    category_id: str = Form(...),
    is_upcoming: bool = Form(False),
    image: UploadFile = File(None)
):
    """
    Admin endpoint to create a new service entry with an optional image.
    """
    service_ref = db.collection("services").document()
    service_id = service_ref.id
    image_url = None
    if image:
        filename = image.filename or f"{uuid4()}.jpg"
        blob = bucket.blob(f"services/{service_id}/{filename}")
        try:
            blob.upload_from_file(image.file, content_type=image.content_type)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Image upload failed: {e}")
        try:
            blob.make_public()
            image_url = blob.public_url
        except Exception:
            image_url = blob.generate_signed_url(expiration=3600*24*365*10)
    data = {
        "title": title,
        "description": description,
        "image": image_url,
        "category_id": category_id,
        "is_upcoming": is_upcoming,
        "is_deleted": False,
        "created_at": None
    }
    service_ref.set(data)
    data['id'] = service_id
    return data

@admin_router.put("/{service_id}", response_model=ServiceOut)
async def update_service(
    service_id: str,
    title: str = Form(None),
    description: str = Form(None),
    category_id: str = Form(None),
    is_upcoming: bool = Form(None),
    image: UploadFile = File(None)
):
    """
    Admin endpoint to update a service.
    Allows updating fields and replacing the image.
    """
    doc_ref = db.collection("services").document(service_id)
    doc = doc_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Service not found")
    update_data = {}
    if title is not None: update_data["title"] = title
    if description is not None: update_data["description"] = description
    if category_id is not None: update_data["category_id"] = category_id
    if is_upcoming is not None: update_data["is_upcoming"] = is_upcoming
    if image is not None:
        # Upload new image and update URL
        filename = image.filename or f"{uuid4()}.jpg"
        blob = bucket.blob(f"services/{service_id}/{filename}")
        try:
            blob.upload_from_file(image.file, content_type=image.content_type)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Image upload failed: {e}")
        try:
            blob.make_public()
            image_url = blob.public_url
        except Exception:
            image_url = blob.generate_signed_url(expiration=3600*24*365*10)
        update_data["image"] = image_url
    if update_data:
        doc_ref.update(update_data)
    updated = doc_ref.get().to_dict()
    updated['id'] = service_id
    return updated

@admin_router.delete("/{service_id}")
def delete_service(service_id: str, hard: bool = False):
    """
    Admin endpoint to delete a service (soft or hard).
    """
    doc_ref = db.collection("services").document(service_id)
    doc = doc_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Service not found")
    if hard:
        doc_ref.delete()
    else:
        doc_ref.update({"is_deleted": True})
    return {"detail": f"Service {'hard ' if hard else ''}deleted"}
