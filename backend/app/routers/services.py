# backend/app/routers/services.py
from fastapi import (
    APIRouter, Depends, HTTPException, File, UploadFile, Form,
    status, Response, Query, Request
)
from typing import List, Dict, Any, Optional
from uuid import uuid4
from enum import Enum
from datetime import datetime

from backend.app.config import db, bucket
from backend.app.core.security import get_current_admin
from backend.app.schemas.service import ServiceOut
from backend.app.schemas.pagination import CursorPage

from firebase_admin import firestore
from google.cloud.firestore_v1 import FieldFilter
from google.cloud import firestore as gcf

router = APIRouter(prefix="/services", tags=["Services"])
admin_router = APIRouter(
    prefix="/services",
    tags=["Admin: Services"],
    dependencies=[Depends(get_current_admin)]
)

MAX_IMAGES = 3


class UpcomingAction(str, Enum):
    keep = "keep"   # update'te dokunma
    yes = "yes"     # True
    no = "no"       # False


def _public_url_for_blob(blob) -> str:
    try:
        blob.make_public()
        return blob.public_url
    except Exception:
        return blob.generate_signed_url(expiration=3600 * 24 * 365 * 10)


def _is_valid_upload_file(file: UploadFile) -> bool:
    """UploadFile'ın gerçekten yüklenmiş bir dosya olup olmadığını kontrol eder"""
    return file is not None and file.filename is not None and file.filename != ""


def _upload_one_image(service_id: str, file: UploadFile) -> Dict[str, str]:
    filename = file.filename or f"{uuid4()}.jpg"
    key = f"services/{service_id}/{uuid4()}-{filename}"
    blob = bucket.blob(key)
    try:
        blob.upload_from_file(file.file, content_type=file.content_type)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Image upload failed: {e}")
    url = _public_url_for_blob(blob)
    return {"url": url, "path": key}


def _normalize_images_fields(doc: Dict[str, Any]) -> Dict[str, Any]:
    """
    Firestore dokümanında:
    - new format: image_items=[{url,path}] veya images=[url]
    - old format: image=url

    Response için:
    - images=[url]
    - image=first_url (geri uyum)
    """
    doc = doc or {}
    image_items = doc.get("image_items")
    images = doc.get("images")

    if isinstance(image_items, list) and image_items:
        urls = [
            x.get("url") for x in image_items
            if isinstance(x, dict) and x.get("url")
        ]
        doc["images"] = urls
        doc["image"] = urls[0] if urls else doc.get("image")
        return doc

    if isinstance(images, list) and images:
        urls = [x for x in images if isinstance(x, str) and x]
        doc["images"] = urls
        doc["image"] = urls[0] if urls else doc.get("image")
        return doc

    if doc.get("image"):
        doc["images"] = [doc["image"]]
    else:
        doc["images"] = []
    return doc


def _service_to_out(doc_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    data = data or {}
    data["id"] = doc_id
    data = _normalize_images_fields(data)
    return data


def _list_services_impl(
    response: Response,
    limit: int = 20,
    start_after: Optional[str] = None,
):
    """
    Servisleri cursor-based pagination ile listeler.
    - is_deleted=False
    - created_at DESC sıralama
    - cursor-based pagination (start_after + limit)
    """
    col = db.collection("services")
    q = col.where(filter=FieldFilter("is_deleted", "==", False))
    try:
        q = q.order_by("created_at", direction=gcf.Query.DESCENDING)
    except Exception:
        pass

    # Cursor-based pagination
    if start_after:
        try:
            cursor_dt = datetime.fromisoformat(start_after.replace("Z", "+00:00"))
            q = q.start_after({"created_at": cursor_dt})
        except Exception:
            pass  # Geçersiz cursor → baştan başla

    # limit + 1 ile sorgula: fazladan 1 tane çekerek has_more hesapla
    docs = list(q.limit(limit + 1).stream())
    response.headers["Cache-Control"] = "public, max-age=60"

    has_more = len(docs) > limit
    docs = docs[:limit]

    out = []
    for d in docs:
        out.append(_service_to_out(d.id, d.to_dict()))

    # next_cursor: son öğenin created_at'ı
    next_cursor = None
    if has_more and docs:
        last_data = docs[-1].to_dict() or {}
        last_ct = last_data.get("created_at")
        if isinstance(last_ct, datetime):
            next_cursor = last_ct.isoformat()

    return CursorPage[ServiceOut](
        items=out,
        count=len(out),
        next_cursor=next_cursor,
        has_more=has_more,
    )


# -------------------------
# PUBLIC LIST
# -------------------------
@router.get("", response_model=CursorPage[ServiceOut], response_model_exclude_none=True, summary="List Services")
def list_services_no_slash(
    response: Response,
    limit: int = Query(20, ge=1, le=100, description="Sayfa başına servis sayısı"),
    start_after: Optional[str] = Query(None, description="ISO datetime cursor (önceki sayfanın next_cursor değeri)"),
):
    return _list_services_impl(response, limit, start_after)


@router.get("/", response_model=CursorPage[ServiceOut], response_model_exclude_none=True, summary="List Services")
def list_services_with_slash(
    response: Response,
    limit: int = Query(20, ge=1, le=100, description="Sayfa başına servis sayısı"),
    start_after: Optional[str] = Query(None, description="ISO datetime cursor (önceki sayfanın next_cursor değeri)"),
):
    return _list_services_impl(response, limit, start_after)


# -------------------------
# ADMIN LIST
# -------------------------
@admin_router.get("/", response_model=list[ServiceOut], response_model_exclude_none=True)
def list_services_admin():
    docs = db.collection("services").stream()
    out = []
    for doc in docs:
        out.append(_service_to_out(doc.id, doc.to_dict()))
    return out


@admin_router.get("", response_model=list[ServiceOut], response_model_exclude_none=True)
def list_services_admin_no_slash():
    return list_services_admin()


# -------------------------
# ADMIN CREATE
# - 3 ayrı dosya seçimi (boş bırakılabilir)
# - foto seçmezsen images=[] döner
# -------------------------
@admin_router.post(
    "/",
    response_model=ServiceOut,
    status_code=status.HTTP_201_CREATED,
    response_model_exclude_none=True
)
async def create_service(
    title: str = Form(...),
    description: str = Form(""),
    is_upcoming: bool = Form(False),

    # KRİTİK: Optional kullanmıyoruz (Swagger textbox bug olmasın)
    image1: UploadFile = File(None, description="Görsel 1 (optional)"),
    image2: UploadFile = File(None, description="Görsel 2 (optional)"),
    image3: UploadFile = File(None, description="Görsel 3 (optional)"),
):
    svc_ref = db.collection("services").document()

    files = [f for f in (image1, image2, image3) if _is_valid_upload_file(f)]
    if len(files) > MAX_IMAGES:
        raise HTTPException(status_code=422, detail=f"Max {MAX_IMAGES} images allowed")

    image_items = []
    for f in files:
        image_items.append(_upload_one_image(svc_ref.id, f))

    payload = {
        "title": title.strip(),
        "description": (description or "").strip(),
        "is_upcoming": bool(is_upcoming),
        "is_deleted": False,
        "created_at": firestore.SERVER_TIMESTAMP,
        "kind": "service",

        # storage-safe format
        "image_items": image_items,

        # backward compat fields
        "images": [x["url"] for x in image_items],
        "image": image_items[0]["url"] if image_items else None,
    }

    svc_ref.set(payload)
    snap = svc_ref.get()
    return _service_to_out(snap.id, snap.to_dict())


@admin_router.post(
    "",
    response_model=ServiceOut,
    status_code=status.HTTP_201_CREATED,
    response_model_exclude_none=True
)
async def create_service_no_slash(
    title: str = Form(...),
    description: str = Form(""),
    is_upcoming: bool = Form(False),
    image1: UploadFile = File(None, description="Görsel 1 (optional)"),
    image2: UploadFile = File(None, description="Görsel 2 (optional)"),
    image3: UploadFile = File(None, description="Görsel 3 (optional)"),
):
    return await create_service(title, description, is_upcoming, image1, image2, image3)


# -------------------------
# ADMIN UPDATE
# - title/description: sadece gönderilirse güncellenir
# - is_upcoming: dropdown (keep/yes/no) -> keep seçilirse dokunmaz
# - 3 dosya seçimi: create gibi Choose File
# - remove_image1/2/3: true ise ilgili slotu siler (dosya seçilmediyse)
# -------------------------
@admin_router.put("/{service_id}", response_model=ServiceOut, response_model_exclude_none=True)
async def update_service(
    service_id: str,
    request: Request,

    # Text alanları: Swagger'da input + "Send empty value" ile omit edilebilir
    title: str = Form(""),
    description: str = Form(""),

    # Seçmeli dropdown: keep / yes / no
    is_upcoming: UpcomingAction = Form(UpcomingAction.keep),

    # Dosya alanları (Choose File)
    image1: UploadFile = File(None, description="Görsel 1 (optional)"),
    image2: UploadFile = File(None, description="Görsel 2 (optional)"),
    image3: UploadFile = File(None, description="Görsel 3 (optional)"),

    # Silme (true/false dropdown/checkbox)
    remove_image1: bool = Form(False),
    remove_image2: bool = Form(False),
    remove_image3: bool = Form(False),
):
    doc_ref = db.collection("services").document(service_id)
    snap = doc_ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Service not found")

    data = snap.to_dict() or {}

    # mevcutları image_items formatına normalize et
    items = data.get("image_items")
    if not isinstance(items, list):
        normalized = _normalize_images_fields(data)
        # path yoksa storage delete yapılamaz, url-only item
        items = [{"url": u, "path": None} for u in normalized.get("images", [])]

    # yardımcı: slot replace / append
    def _replace_at(slot_idx: int, file: UploadFile):
        nonlocal items
        if slot_idx < len(items):
            old = items[slot_idx]
            old_path = old.get("path") if isinstance(old, dict) else None
            if old_path:
                try:
                    bucket.blob(old_path).delete()
                except Exception:
                    pass
            items[slot_idx] = _upload_one_image(service_id, file)
        else:
            items.append(_upload_one_image(service_id, file))

    # silme: sadece remove true ve aynı slota yeni dosya yoksa
    if remove_image3 and not _is_valid_upload_file(image3) and len(items) > 2:
        removed = items.pop(2)
        path = removed.get("path") if isinstance(removed, dict) else None
        if path:
            try:
                bucket.blob(path).delete()
            except Exception:
                pass

    if remove_image2 and not _is_valid_upload_file(image2) and len(items) > 1:
        removed = items.pop(1)
        path = removed.get("path") if isinstance(removed, dict) else None
        if path:
            try:
                bucket.blob(path).delete()
            except Exception:
                pass

    if remove_image1 and not _is_valid_upload_file(image1) and len(items) > 0:
        removed = items.pop(0)
        path = removed.get("path") if isinstance(removed, dict) else None
        if path:
            try:
                bucket.blob(path).delete()
            except Exception:
                pass

    # yeni upload varsa replace
    if _is_valid_upload_file(image1):
        _replace_at(0, image1)
    if _is_valid_upload_file(image2):
        _replace_at(1, image2)
    if _is_valid_upload_file(image3):
        _replace_at(2, image3)

    # max 3
    if len(items) > MAX_IMAGES:
        items = items[:MAX_IMAGES]

    # Formda hangi alanlar gerçekten gönderilmiş -> buna göre update
    form = await request.form()
    update_data: Dict[str, Any] = {}

    if "title" in form:
        update_data["title"] = title.strip()
    if "description" in form:
        update_data["description"] = (description or "").strip()

    # is_upcoming dropdown: keep ise dokunma
    if "is_upcoming" in form and is_upcoming != UpcomingAction.keep:
        update_data["is_upcoming"] = (is_upcoming == UpcomingAction.yes)

    urls = [x.get("url") for x in items if isinstance(x, dict) and x.get("url")]
    update_data["image_items"] = items
    update_data["images"] = urls
    update_data["image"] = urls[0] if urls else None

    doc_ref.update(update_data)
    out = doc_ref.get().to_dict() or {}
    return _service_to_out(service_id, out)


# -------------------------
# ADMIN DELETE (service)
# -------------------------
@admin_router.delete("/{service_id}")
def delete_service(service_id: str, hard: bool = False):
    doc_ref = db.collection("services").document(service_id)
    snap = doc_ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Service not found")

    if hard:
        data = snap.to_dict() or {}
        items = data.get("image_items") or []
        if isinstance(items, list):
            for it in items:
                path = it.get("path") if isinstance(it, dict) else None
                if path:
                    try:
                        bucket.blob(path).delete()
                    except Exception:
                        pass
        doc_ref.delete()
        return {"detail": "Service hard deleted"}

    doc_ref.update({"is_deleted": True})
    return {"detail": "Service deleted"}


# -------------------------
# ADMIN: delete single image by index
# DELETE /admin/services/{id}/images?index=0
# -------------------------
@admin_router.delete("/{service_id}/images", response_model=ServiceOut, response_model_exclude_none=True)
def delete_service_image(service_id: str, index: int = Query(..., ge=0)):
    doc_ref = db.collection("services").document(service_id)
    snap = doc_ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Service not found")

    data = snap.to_dict() or {}
    items = data.get("image_items")
    if not isinstance(items, list):
        normalized = _normalize_images_fields(data)
        items = [{"url": u, "path": None} for u in normalized.get("images", [])]

    if index < 0 or index >= len(items):
        raise HTTPException(status_code=422, detail="Invalid image index")

    removed = items.pop(index)
    path = removed.get("path") if isinstance(removed, dict) else None
    if path:
        try:
            bucket.blob(path).delete()
        except Exception:
            pass

    urls = [x.get("url") for x in items if isinstance(x, dict) and x.get("url")]
    doc_ref.update({
        "image_items": items,
        "images": urls,
        "image": urls[0] if urls else None
    })

    out = doc_ref.get().to_dict() or {}
    return _service_to_out(service_id, out)
