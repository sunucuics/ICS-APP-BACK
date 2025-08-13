"""
app/routers/services.py - Routes for service listing (public) and management (admin).
Flat collection: services/{id} (no categories).
"""
from fastapi import APIRouter, Depends, HTTPException, File, UploadFile, Form, status
from typing import List, Optional
from uuid import uuid4

from app.config import db, bucket
from app.core.security import get_current_admin
from app.schemas.service import ServiceOut
from firebase_admin import firestore
from google.cloud.firestore_v1 import FieldFilter
from google.cloud import firestore as gcf  # for Query.DESCENDING

# Public router => GET /services/
router = APIRouter(prefix="/services", tags=["Services"])

@router.get("/", response_model=List[ServiceOut], response_model_exclude_none=True)
def list_services():
    """
    Return all non-deleted services (no query params).
    """
    col = db.collection("services")
    q = col.where(filter=FieldFilter("is_deleted", "==", False))
    # created_at olmayan eski kayıtlar için order_by korumalı
    try:
        q = q.order_by("created_at", direction=gcf.Query.DESCENDING)
    except Exception:
        pass

    docs = list(q.stream())
    return [{**d.to_dict(), "id": d.id} for d in docs]

# Admin sub-router => /admin/services/...
admin_router = APIRouter(prefix="/services", tags=["Admin: Services"], dependencies=[Depends(get_current_admin)])

@admin_router.post("/", response_model=ServiceOut, status_code=status.HTTP_201_CREATED, response_model_exclude_none=True)
async def create_service(
    title: str = Form(...),
    description: str = Form(""),
    is_upcoming: bool = Form(False),
    image: UploadFile = File(...),
):
    """
    Create a service (flat services/{id}). No category.
    """
    svc_ref = db.collection("services").document()

    # Upload image
    filename = image.filename or f"{uuid4()}.jpg"
    blob = bucket.blob(f"services/{svc_ref.id}/{filename}")
    blob.upload_from_file(image.file, content_type=image.content_type)
    try:
        blob.make_public()
        image_url = blob.public_url
    except Exception:
        image_url = blob.generate_signed_url(expiration=3600 * 24 * 365 * 10)

    payload = {
        "id": svc_ref.id,
        "title": title.strip(),
        "description": description.strip(),
        "image": image_url,
        "is_upcoming": is_upcoming,
        "is_deleted": False,
        "created_at": firestore.SERVER_TIMESTAMP,  # depoda server zamanı
        "kind": "service",
    }
    svc_ref.set(payload)

    # Timestamp'ı somutlaştırarak dön
    snap = svc_ref.get()
    data = snap.to_dict() or {}
    data["id"] = snap.id
    return data

@admin_router.put("/{service_id}", response_model=ServiceOut, response_model_exclude_none=True)
async def update_service(
    service_id: str,
    title: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    is_upcoming: Optional[bool] = Form(None),
    image: Optional[UploadFile] = File(None),
):
    """
    Update a service (title/description/is_upcoming/image). No category fields.
    """
    doc_ref = db.collection("services").document(service_id)
    snap = doc_ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Service not found")

    update_data = {}
    if title is not None:
        update_data["title"] = title.strip()
    if description is not None:
        update_data["description"] = description.strip()
    if is_upcoming is not None:
        update_data["is_upcoming"] = is_upcoming

    if image is not None:
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
            image_url = blob.generate_signed_url(expiration=3600 * 24 * 365 * 10)
        update_data["image"] = image_url

    if update_data:
        doc_ref.update(update_data)

    out = doc_ref.get().to_dict() or {}
    out["id"] = service_id
    return out

@admin_router.delete("/{service_id}")
def delete_service(service_id: str, hard: bool = False):
    """
    Soft delete by default; hard delete if hard=true.
    """
    doc_ref = db.collection("services").document(service_id)
    snap = doc_ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Service not found")

    if hard:
        doc_ref.delete()
        return {"detail": "Service hard deleted"}
    else:
        doc_ref.update({"is_deleted": True})
        return {"detail": "Service deleted"}
