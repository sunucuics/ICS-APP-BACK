"""# `app/routers/comments.py` — Yorum Yönetimi Dokümantasyonu

## Genel Bilgi
Bu dosya, kullanıcıların genel yorum eklemesini, yorumları listelemesini ve admin paneli üzerinden yorum yönetimi yapılmasını sağlar.
Yorumlar ürün veya hizmet tipinde olabilir, puanlama (1–5) ve içerik metni içerir.

---

## Kullanıcı Tarafı Endpoint’ler

### `POST /comments/`
**Amaç:** Genel yorum eklemek (3 kutu: `target_type`, `rating`, `content`).

**Parametreler (Form-Data):**
- `target_type`: `"product"` veya `"service"`
- `rating`: 1–5 arası tamsayı
- `content`: 1–500 karakter arası metin

**İşleyiş:**
1. `get_current_user` ile kullanıcı doğrulaması yapılır.
2. `settings/profanity` dokümanındaki yasaklı kelimeler kontrol edilir.
3. Firestore `comments` koleksiyonuna yeni yorum eklenir:
   - `target_id` = `"__all__"` → genel yorum işareti
   - `created_at` = Sunucu zaman damgası
   - `is_deleted` = `False`
4. Oluşan yorum `id` ile birlikte döndürülür.

---

### `GET /comments/`
**Amaç:** Yorumları listelemek (opsiyonel filtrelerle).

**Parametreler (Query):**
- `target_type`: `"product"` veya `"service"` (opsiyonel)
- `only_general`: `true` ise sadece genel yorumlar
- `limit`: 1–200 arası, varsayılan 50

**İşleyiş:**
1. `is_deleted = False` olan yorumlar çekilir.
2. `target_type` varsa filtrelenir.
3. `only_general=true` ise `target_id="__all__"` filtrelenir.
4. `created_at` alanına göre azalan sıralama yapılır.
5. Sonuç listesi `id` eklenerek döndürülür.

---

## Admin Tarafı Endpoint’ler

### `GET /comments/` *(Admin)*
**Amaç:** Admin olarak yorum listesini parametresiz almak.

**İşleyiş:**
1. `is_deleted = False` olan yorumlar çekilir.
2. `created_at`’e göre azalan sıralama yapılır (mümkünse).
3. Maksimum 100 kayıt döndürülür.

---

### `DELETE /comments/{comment_id}` *(Admin)*
**Amaç:** Yorum silmek (soft veya hard delete).

**Parametreler:**
- `comment_id`: Silinecek yorumun ID’si
- `hard`: `true` ise kalıcı silme, `false` ise soft delete

**İşleyiş:**
1. Yorum dokümanı çekilir, yoksa `404` döner.
2. `hard=true` ise doküman Firestore’dan tamamen silinir.
3. `hard=false` ise `is_deleted=True` olarak işaretlenir.
4. Silme işlemi sonucu döndürülür.
"""

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
