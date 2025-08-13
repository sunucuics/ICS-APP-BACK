from typing import List, Optional, Literal
from fastapi import APIRouter, Depends, HTTPException, Form, Query
from pydantic import conint, constr
from firebase_admin import firestore
from google.cloud.firestore_v1 import FieldFilter
from google.cloud import firestore as gcf

from app.config import db
from app.core.security import get_current_user, get_current_admin
from app.schemas.comment import CommentOut

router = APIRouter(prefix="/comments", tags=["Comments"])

# 1) 3 kutucukla yorum ekle (GENEL yorum)
@router.post("/", response_model=CommentOut, response_model_exclude_none=True, summary="Yorum ekle (3 kutu)")
def create_comment_simple(
    target_type: Literal["product", "service"] = Form(..., description='Ürünlere mi, hizmetlere mi?'),
    rating: conint(ge=1, le=5) = Form(..., description='Puan (1..5)'),
    content: constr(min_length=1, max_length=500) = Form(..., description='Yorum (max 500)'),
    current_user: dict = Depends(get_current_user),
):
    """
    *Tam 3 alan:* **target_type**, **rating**, **content**.
    Bu uç **genel** yorum içindir; belirli ürün/hizmete bağlı değildir.
    """
    user_id = current_user["id"]

    # Basit küfür filtresi
    prof = db.collection("settings").document("profanity").get()
    blocked = (prof.to_dict() or {}).get("blocked_words", []) if prof.exists else []
    low = content.lower()
    if any(bw.lower() in low for bw in blocked):
        raise HTTPException(status_code=400, detail="Uygunsuz içerik tespit edildi.")

    ref = db.collection("comments").document()
    data = {
        "target_type": target_type,
        "target_id": "__all__",               # genel yorum işareti
        "user_id": user_id,
        "rating": int(rating),
        "content": content,
        "is_deleted": False,
        "created_at": firestore.SERVER_TIMESTAMP,
    }
    ref.set(data)
    snap = ref.get()
    out = snap.to_dict() or {}
    out["id"] = ref.id
    return out

# 2) Listeleme (opsiyonel filtreler)
@router.get("/", response_model=List[CommentOut], summary="Yorumları listele")
def list_comments(
    target_type: Optional[Literal["product","service"]] = Query(None),
    only_general: bool = Query(False, description="Sadece genel yorumlar (__all__)"),
    limit: int = Query(50, ge=1, le=200),
):
    q = db.collection("comments").where(filter=FieldFilter("is_deleted", "==", False))
    if target_type:
        q = q.where(filter=FieldFilter("target_type", "==", target_type))
    if only_general:
        q = q.where(filter=FieldFilter("target_id", "==", "__all__"))
    try:
        q = q.order_by("created_at", direction=gcf.Query.DESCENDING)
    except Exception:
        pass
    docs = list(q.limit(limit).stream())
    return [{**d.to_dict(), "id": d.id} for d in docs]


# -------------------- ADMIN --------------------
admin_router = APIRouter(prefix="/comments", tags=["Admin: Comments"], dependencies=[Depends(get_current_admin)])

@admin_router.get("/", response_model=List[CommentOut], summary="(Admin) yorum listesi (parametresiz)")
def admin_list_comments():
    """
    Parametresiz admin listesi:
    - Sadece **is_deleted = False** yorumlar
    - `created_at`'e göre **son eklenenler en üstte**
    - Varsayılan limit: **100**
    """
    q = (
        db.collection("comments")
          .where(filter=FieldFilter("is_deleted", "==", False))
    )
    try:
        q = q.order_by("created_at", direction=gcf.Query.DESCENDING)
    except Exception:
        pass  # created_at olmayan eski kayıtlar varsa sıralamasız getir

    docs = list(q.limit(100).stream())
    return [{**d.to_dict(), "id": d.id} for d in docs]

@admin_router.delete("/{comment_id}", summary="(Admin) sil")
def admin_delete_comment(comment_id: str, hard: bool = False):
    ref = db.collection("comments").document(comment_id)
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Comment not found")
    if hard:
        ref.delete()
        return {"detail": "Comment hard deleted"}
    ref.update({"is_deleted": True})
    return {"detail": "Comment deleted"}
