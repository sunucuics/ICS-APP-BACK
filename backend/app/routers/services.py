from fastapi import APIRouter, Depends, HTTPException, File, UploadFile, Form, status, Response, Query
from typing import List, Optional
from uuid import uuid4

from backend.app.config import db, bucket
from backend.app.core.security import get_current_admin
from backend.app.schemas.service import ServiceOut
from firebase_admin import firestore
from google.cloud.firestore_v1 import FieldFilter
from google.cloud import firestore as gcf  # for Query.DESCENDING


router = APIRouter(prefix="/services", tags=["Services"])
admin_router = APIRouter(prefix="/services", tags=["Admin: Services"], dependencies=[Depends(get_current_admin)])


# ----------------------------- Helpers ---------------------------------- #

def _public_or_signed_url(blob) -> str:
    """
    Public yapmayı dener; olmazsa uzun süreli signed URL üretir.
    """
    try:
        blob.make_public()
        return blob.public_url
    except Exception:
        return blob.generate_signed_url(expiration=3600 * 24 * 365 * 10)  # ~10 yıl


def _upload_service_image(service_id: str, upload: UploadFile, slot: int) -> str:
    """
    slot: 1/2/3
    Storage path: services/{service_id}/{slot}_{uuid}_{filename}
    """
    if slot not in (1, 2, 3):
        raise ValueError("slot must be 1, 2 or 3")

    original = (upload.filename or "image").replace("/", "_").replace("\\", "_")
    filename = f"{slot}_{uuid4()}_{original}"
    blob = bucket.blob(f"services/{service_id}/{filename}")

    try:
        blob.upload_from_file(upload.file, content_type=upload.content_type)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Image{slot} upload failed: {e}")

    return _public_or_signed_url(blob)


def _delete_all_service_blobs(service_id: str) -> int:
    """
    services/{service_id}/ prefix altındaki tüm dosyaları siler.
    """
    prefix = f"services/{service_id}/"
    deleted = 0
    try:
        for b in bucket.list_blobs(prefix=prefix):
            try:
                b.delete()
                deleted += 1
            except Exception:
                # tek bir blob silinemezse tüm işlemi patlatmayalım
                pass
    except Exception:
        pass
    return deleted


def _normalize_service_doc(d: dict, doc_id: str) -> dict:
    """
    Firestore dokümanını frontend için normalize eder:
    - images alanı yoksa image üzerinden türetir
    - image alanı yoksa images[0] üzerinden türetir
    """
    out = dict(d or {})
    out["id"] = doc_id

    images = out.get("images")
    image = out.get("image")

    if not isinstance(images, list) or any(not isinstance(x, str) for x in images):
        images = []
        if isinstance(image, str) and image:
            images = [image]

    # image alanını da garanti et (geri uyum)
    if not (isinstance(image, str) and image):
        if images:
            out["image"] = images[0]

    out["images"] = images
    return out


# ----------------------------- Public list ------------------------------- #

def _list_services_impl(response: Response):
    col = db.collection("services")
    q = col.where(filter=FieldFilter("is_deleted", "==", False))
    try:
        q = q.order_by("created_at", direction=gcf.Query.DESCENDING)
    except Exception:
        pass

    docs = list(q.limit(50).stream())  # istersen 20/100 yap
    response.headers["Cache-Control"] = "public, max-age=60"

    return [_normalize_service_doc(d.to_dict(), d.id) for d in docs]


@router.get("", response_model=List[ServiceOut], response_model_exclude_none=True, summary="List Services")
def list_services_no_slash(response: Response):
    return _list_services_impl(response)


@router.get("/", response_model=List[ServiceOut], response_model_exclude_none=True, summary="List Services")
def list_services_with_slash(response: Response):
    return _list_services_impl(response)


# ----------------------------- Admin list -------------------------------- #

@admin_router.get("/", response_model=List[ServiceOut], response_model_exclude_none=True)
def list_services_admin():
    services_ref = db.collection("services")
    docs = services_ref.stream()
    services: List[ServiceOut] = []
    for doc in docs:
        service_data = _normalize_service_doc(doc.to_dict(), doc.id)
        services.append(ServiceOut(**service_data))
    return services


@admin_router.get("", response_model=List[ServiceOut], response_model_exclude_none=True)
def list_services_admin_no_slash():
    return list_services_admin()


# ----------------------------- Admin create ------------------------------ #
# Swagger’da image1/image2/image3 zorunlu gözüksün diye required yaptık.

@admin_router.post("/", response_model=ServiceOut, status_code=status.HTTP_201_CREATED, response_model_exclude_none=True)
async def create_service(
    title: str = Form(...),
    description: str = Form(""),
    is_upcoming: bool = Form(False),
    image1: UploadFile = File(..., description="Görsel 1 (zorunlu)"),
    image2: UploadFile = File(..., description="Görsel 2 (zorunlu)"),
    image3: UploadFile = File(..., description="Görsel 3 (zorunlu)"),
):
    svc_ref = db.collection("services").document()
    service_id = svc_ref.id

    url1 = _upload_service_image(service_id, image1, 1)
    url2 = _upload_service_image(service_id, image2, 2)
    url3 = _upload_service_image(service_id, image3, 3)

    payload = {
        "id": service_id,
        "title": title.strip(),
        "description": (description or "").strip(),
        "images": [url1, url2, url3],
        "image": url1,  # geri uyum
        "is_upcoming": bool(is_upcoming),
        "is_deleted": False,
        "created_at": firestore.SERVER_TIMESTAMP,
        "kind": "service",
    }
    svc_ref.set(payload)

    snap = svc_ref.get()
    return _normalize_service_doc(snap.to_dict(), snap.id)


@admin_router.post("", response_model=ServiceOut, status_code=status.HTTP_201_CREATED, response_model_exclude_none=True)
async def create_service_no_slash(
    title: str = Form(...),
    description: str = Form(""),
    is_upcoming: bool = Form(False),
    image1: UploadFile = File(...),
    image2: UploadFile = File(...),
    image3: UploadFile = File(...),
):
    return await create_service(title, description, is_upcoming, image1, image2, image3)


# ----------------------------- Admin update ------------------------------ #

@admin_router.put("/{service_id}", response_model=ServiceOut, response_model_exclude_none=True)
async def update_service(
    service_id: str,
    title: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    is_upcoming: Optional[bool] = Form(None),

    image1: Optional[UploadFile] = File(None),
    image2: Optional[UploadFile] = File(None),
    image3: Optional[UploadFile] = File(None),

    remove_image1: bool = Form(False),
    remove_image2: bool = Form(False),
    remove_image3: bool = Form(False),
):
    doc_ref = db.collection("services").document(service_id)
    snap = doc_ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Service not found")

    current = _normalize_service_doc(snap.to_dict(), service_id)
    images = list(current.get("images") or [])

    # images listesi en az 3 slot olacak şekilde genişlet
    while len(images) < 3:
        images.append("")

    update_data = {}

    if title is not None:
        update_data["title"] = title.strip()
    if description is not None:
        update_data["description"] = (description or "").strip()
    if is_upcoming is not None:
        update_data["is_upcoming"] = bool(is_upcoming)

    # Remove flags
    if remove_image1:
        images[0] = ""
    if remove_image2:
        images[1] = ""
    if remove_image3:
        images[2] = ""

    # Upload replacements
    if image1 is not None:
        images[0] = _upload_service_image(service_id, image1, 1)
    if image2 is not None:
        images[1] = _upload_service_image(service_id, image2, 2)
    if image3 is not None:
        images[2] = _upload_service_image(service_id, image3, 3)

    # Trim trailing empties ama aradaki boşları koru (slot düzeni bozulmasın)
    # Örn: ["url1", "", "url3"] valid
    update_data["images"] = images

    # geri uyum: image = ilk dolu url
    first = next((u for u in images if isinstance(u, str) and u), "")
    update_data["image"] = first if first else ""

    doc_ref.update(update_data)

    out = _normalize_service_doc(doc_ref.get().to_dict(), service_id)
    return out


# ----------------------------- Admin delete ------------------------------ #

@admin_router.delete("/{service_id}")
def delete_service(service_id: str, hard: bool = False):
    doc_ref = db.collection("services").document(service_id)
    snap = doc_ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Service not found")

    if hard:
        deleted_files = _delete_all_service_blobs(service_id)
        doc_ref.delete()
        return {"detail": "Service hard deleted", "deleted_files": deleted_files}
    else:
        doc_ref.update({"is_deleted": True})
        return {"detail": "Service deleted (soft)"}
