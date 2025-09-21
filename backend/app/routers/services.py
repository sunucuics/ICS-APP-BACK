"""
# `app/routers/services.py` — Hizmet Yönetimi Dokümantasyonu

## Genel Bilgi
Bu dosya, herkese açık hizmet listeleme ve admin panelinden hizmet ekleme, güncelleme, silme işlemlerini içerir.
Hizmetler `services/{id}` şeklinde tek seviyeli (flat) bir koleksiyonda saklanır.

---

## Kullanıcı Tarafı Endpoint

### `GET /services/`
**Amaç:** Tüm silinmemiş hizmetleri listelemek.

**İşleyiş:**
1. `services` koleksiyonundan `is_deleted=False` olan kayıtlar çekilir.
2. `created_at` alanına göre azalan sıralama yapılmaya çalışılır.
3. Her hizmet `id` alanı eklenerek döndürülür.

---

## Admin Tarafı Endpoint’ler

### `POST /services/`
**Amaç:** Yeni hizmet eklemek.

**Parametreler (Form-Data + File):**
- `title`: Başlık (zorunlu)
- `description`: Açıklama (opsiyonel, varsayılan boş)
- `is_upcoming`: Yakında mı? (varsayılan `False`)
- `image`: Zorunlu görsel

**İşleyiş:**
1. Firestore’da `services/{id}` dokümanı oluşturulur.
2. Görsel Firebase Storage’a yüklenir, public URL veya signed URL alınır.
3. Firestore’a hizmet verisi kaydedilir (`is_deleted=False`, `created_at`=timestamp).
4. Kaydedilen hizmet `id` ile döndürülür.

---

### `PUT /services/{service_id}`
**Amaç:** Mevcut hizmeti güncellemek.

**Parametreler (Form-Data + File):**
- `title`: Başlık
- `description`: Açıklama
- `is_upcoming`: Yakında mı?
- `image`: Yeni görsel (varsa mevcut görselin yerine geçer)

**İşleyiş:**
1. Firestore’dan hizmet dokümanı çekilir, yoksa `404` döner.
2. Gönderilen alanlar güncellenir.
3. Yeni görsel yüklenirse Storage’a yüklenir ve URL güncellenir.
4. Güncellenmiş hizmet bilgisi döndürülür.

---

### `DELETE /services/{service_id}`
**Amaç:** Hizmet silmek (soft veya hard delete).

**Parametreler:**
- `service_id`: Silinecek hizmet ID’si
- `hard`: `true` ise kalıcı silme, `false` ise soft delete

**İşleyiş:**
1. Firestore’dan hizmet dokümanı çekilir, yoksa `404` döner.
2. `hard=true` ise doküman tamamen silinir.
3. `hard=false` ise `is_deleted=True` olarak işaretlenir.
4. Silme işlemi sonucu döndürülür.

"""
from fastapi import APIRouter, Depends, HTTPException, File, UploadFile, Form, status , Response, Query
from typing import List, Optional
from uuid import uuid4

from backend.app.config import db, bucket
from backend.app.core.security import get_current_admin
from backend.app.schemas.service import ServiceOut
from firebase_admin import firestore
from google.cloud.firestore_v1 import FieldFilter
from google.cloud import firestore as gcf  # for Query.DESCENDING

# Public router => GET /services/
router = APIRouter(prefix="/services", tags=["Services"])

def _list_services_impl(response: Response):
    """
    Ana ekran: sadece silinmemiş hizmetleri döner.
    created_at varsa DESC sıralar. Limit sabit (örn. 20).
    """
    col = db.collection("services")
    q = col.where(filter=FieldFilter("is_deleted", "==", False))
    try:
        q = q.order_by("created_at", direction=gcf.Query.DESCENDING)
    except Exception:
        pass  # created_at olmayan eski kayıtlar için

    # İstersen burada 20 yerine 50/100 yapabilirsin
    docs = list(q.limit(20).stream())

    # Küçük cache (opsiyonel)
    response.headers["Cache-Control"] = "public, max-age=60"

    return [{**d.to_dict(), "id": d.id} for d in docs]

# Admin sub-router => /admin/services/...
admin_router = APIRouter(prefix="/services", tags=["Admin: Services"], dependencies=[Depends(get_current_admin)])

@admin_router.get("/", response_model=list[ServiceOut], response_model_exclude_none=True)
def list_services_admin():
    """
    Admin - List all services
    """
    services_ref = db.collection("services")
    docs = services_ref.stream()
    services = []
    for doc in docs:
        service_data = doc.to_dict()
        service_data["id"] = doc.id
        services.append(ServiceOut(**service_data))
    return services

@admin_router.get("", response_model=list[ServiceOut], response_model_exclude_none=True)
def list_services_admin_no_slash():
    """
    Admin - List all services (no trailing slash)
    """
    return list_services_admin()

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
        image_url = blob.generate_signed_url(expiration=3600 * 24 * 365 * 100000)

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


@router.get("", response_model=List[ServiceOut], response_model_exclude_none=True, summary="List Services")
def list_services_no_slash(response: Response):
    """List services endpoint without trailing slash."""
    return _list_services_impl(response)


@router.get("/", response_model=List[ServiceOut], response_model_exclude_none=True, summary="List Services")
def list_services_with_slash(response: Response):
    """List services endpoint with trailing slash."""
    return _list_services_impl(response)
