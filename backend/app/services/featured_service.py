# app/services/featured_service.py
from __future__ import annotations
from typing import Literal, List, Optional, Dict, Any

from firebase_admin import firestore as fb_fs
from google.cloud.firestore_v1 import CollectionReference, Query
from google.cloud.firestore_v1.base_query import FieldFilter  # ✅ uyarısız where()

from backend.app.schemas.featured import FeaturedKind, FeaturedItemOut

db = fb_fs.client()

def _collection(kind: FeaturedKind) -> CollectionReference:
    # featured_products / featured_services
    return db.collection(f"featured_{kind}")

# (opsiyonel) settings ile ürün koleksiyon adı override edilebilsin
try:
    from backend.app.config import settings
    _PRODUCTS_COLLECTION_OVERRIDE = (
        getattr(settings, "products_collection", None)
        or getattr(settings, "product_collection", None)
    )
except Exception:
    _PRODUCTS_COLLECTION_OVERRIDE = None

# Top-level koleksiyon adayları (fallback)
_COLLECTION_CANDIDATES: Dict[str, List[str]] = {
    "services": ["services"],
    "products": list(
        dict.fromkeys([  # tekrarları temizle
            _PRODUCTS_COLLECTION_OVERRIDE,
            "products", "product", "items", "catalog", "inventory",
        ])
    ),
}

# Collection Group için aday isimler (nested ürünler için)
_PRODUCT_GROUP_CANDIDATES: List[str] = ["products", "product", "items", "catalog", "inventory"]

def _doc_exists(coll: CollectionReference, item_id: str) -> bool:
    return coll.document(item_id).get().exists

def _to_dt(ts):
    if ts is None:
        return None
    try:
        return ts.to_datetime()
    except Exception:
        return ts if hasattr(ts, "isoformat") else None

def _normalize_dict(d: Dict[str, Any]) -> Dict[str, Any]:
    """Timestamp benzeri alanları ISO string'e çevirir."""
    out: Dict[str, Any] = {}
    for k, v in (d or {}).items():
        try:
            if hasattr(v, "to_datetime"):
                out[k] = v.to_datetime().isoformat()
            elif hasattr(v, "isoformat") and not isinstance(v, str):
                out[k] = v.isoformat()
            else:
                out[k] = v
        except Exception:
            out[k] = v
    return out

def _find_source_snap(kind: FeaturedKind, item_id: str):
    """
    Kaynak dokümanı bul:
      - PRODUCTS: Önce collection_group(...) ile 'id' veya 'product_id' alanlarından tara,
                  yoksa top-level aday koleksiyonlarda dene.
      - SERVICES: Top-level 'services' (veya aday listesi) içinde ara.
    """
    if kind == "products":
        # 1) Collection Group ile ara (nested koleksiyonlar)
        for cg_name in _PRODUCT_GROUP_CANDIDATES:
            try:
                q = db.collection_group(cg_name).where(filter=FieldFilter("id", "==", item_id)).limit(1)
                for s in q.stream():
                    return s
            except Exception:
                pass
            try:
                q = db.collection_group(cg_name).where(filter=FieldFilter("product_id", "==", item_id)).limit(1)
                for s in q.stream():
                    return s
            except Exception:
                pass

        # 2) Fallback: top-level aday koleksiyonlarda ara
        for name in _COLLECTION_CANDIDATES["products"]:
            if not name:
                continue
            try:
                snap = db.collection(name).document(item_id).get()
                if snap.exists:
                    return snap
            except Exception:
                pass
            try:
                q = db.collection(name).where(filter=FieldFilter("id", "==", item_id)).limit(1)
                for s in q.stream():
                    return s
            except Exception:
                pass
            try:
                q = db.collection(name).where(filter=FieldFilter("product_id", "==", item_id)).limit(1)
                for s in q.stream():
                    return s
            except Exception:
                pass
        return None

    # kind == "services"
    for name in _COLLECTION_CANDIDATES["services"]:
        try:
            snap = db.collection(name).document(item_id).get()
            if snap.exists:
                return snap
        except Exception:
            pass
        try:
            q = db.collection(name).where(filter=FieldFilter("id", "==", item_id)).limit(1)
            for s in q.stream():
                return s
        except Exception:
            pass
        try:
            q = db.collection(name).where(filter=FieldFilter("service_id", "==", item_id)).limit(1)
            for s in q.stream():
                return s
        except Exception:
            pass
    return None

def detail_of(kind: FeaturedKind, item_id: str) -> Optional[Dict[str, Any]]:
    """Kaynak dokümanı getir (services/products)."""
    snap = _find_source_snap(kind, item_id)
    if not snap:
        return None
    data = _normalize_dict(snap.to_dict() or {})
    data["id"] = data.get("id") or snap.id
    return data

def feature(kind: FeaturedKind, item_id: str, admin_uid: Optional[str], expand_detail: bool = False):
    """
    Özelliği ekle (idempotent). expand_detail=True ise kaynak dokümanı döndür.
    """
    coll = _collection(kind)
    doc_ref = coll.document(item_id)
    snap = doc_ref.get()

    if not snap.exists:
        doc_ref.set(
            {"id": item_id, "created_by": admin_uid, "created_at": fb_fs.SERVER_TIMESTAMP}
        )

    if expand_detail:
        return detail_of(kind, item_id) or {"id": item_id}
    current = doc_ref.get().to_dict() or {}
    return FeaturedItemOut(
        id=current.get("id") or item_id,
        created_by=current.get("created_by"),
        created_at=_to_dt(current.get("created_at")),
    )

def unfeature(kind: FeaturedKind, item_id: str) -> None:
    coll = _collection(kind)
    if not _doc_exists(coll, item_id):
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Featured {kind[:-1]} not found.")
    coll.document(item_id).delete()

def list_items(kind: FeaturedKind, expand_detail: bool = False):
    """
    expand_detail=False: FeaturedItemOut list (id, created_by, created_at)
    expand_detail=True : Kaynak doküman listesi (title, description, image, ...)
    """
    coll = _collection(kind)
    try:
        q: Query = coll.order_by("created_at", direction=fb_fs.Query.DESCENDING)
    except Exception:
        q = coll

    if not expand_detail:
        items: List[FeaturedItemOut] = []
        for doc in q.stream():
            d = doc.to_dict() or {}
            items.append(
                FeaturedItemOut(
                    id=d.get("id") or doc.id,
                    created_by=d.get("created_by"),
                    created_at=_to_dt(d.get("created_at")),
                )
            )
        return items

    # Detaylı liste: kaynak koleksiyondan dokümanları çek
    result: List[Dict[str, Any]] = []
    for doc in q.stream():
        item_id = (doc.to_dict() or {}).get("id") or doc.id
        detail = detail_of(kind, item_id) or {"id": item_id}
        result.append(detail)
    return result
