# app/routers/comments.py — Yorum Sistemi (yalın + user_name fix: Firestore + Auth fallback)

from typing import List, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException, Form, Query
from pydantic import conint, constr
from firebase_admin import firestore
from firebase_admin import auth as fb_auth  # <-- Auth fallback
from google.cloud.firestore_v1 import FieldFilter
from google.cloud import firestore as gcf

from backend.app.config import db
from backend.app.core.security import get_current_user, get_current_admin
from backend.app.schemas.comment import CommentOut, TargetType , ProfanityIn , ProfanityWordsIn

router = APIRouter(prefix="/comments", tags=["Comments"])
admin_router = APIRouter(
    prefix="/comments",
    tags=["Admin: Comments"],
    dependencies=[Depends(get_current_admin)],
)

# ---------- Helpers ----------

def _profanity_blocked(content: str) -> bool:
    prof = db.collection("settings").document("profanity").get()
    blocked = (prof.to_dict() or {}).get("blocked_words", []) if prof.exists else []
    low = content.lower()
    return any((bw or "").lower() in low for bw in blocked)

def _pick_name(rec: dict) -> Optional[str]:
    """Kullanıcı belgesinden ad soyad alanını akıllıca seçer."""
    if not rec:
        return None
    # En yaygın kombinasyonlar
    candidates = [
        rec.get("name"),
        rec.get("full_name"),
        " ".join(filter(None, [rec.get("first_name"), rec.get("last_name")])).strip() or None,
        rec.get("display_name"),
        rec.get("displayName"),
    ]
    # Nested profile.* desteği
    prof = rec.get("profile") or {}
    candidates.extend([
        prof.get("name"),
        prof.get("full_name"),
        " ".join(filter(None, [prof.get("first_name"), prof.get("last_name")])).strip() or None,
        prof.get("display_name"),
        prof.get("displayName"),
    ])
    return next((c for c in candidates if c), None)

def _doc_to_out(snap) -> Dict:
    data = snap.to_dict() or {}
    ts = data.get("created_at")
    if hasattr(ts, "to_datetime"):
        data["created_at"] = ts.to_datetime()
    data["id"] = snap.id
    return data

def _load_user_names(uids: List[str]) -> dict:
    """Önce Firestore users/{uid}, yoksa Firebase Auth'tan display_name. {uid: name} döndürür."""
    mapping: Dict[str, Optional[str]] = {}
    uniq = [u for u in set(uids) if u]
    if not uniq:
        return mapping

    # 1) Firestore batch (mümkünse)
    try:
        refs = [db.collection("users").document(uid) for uid in uniq]
        snaps = list(db.get_all(refs))  # bazı sürümlerde destekli
    except Exception:
        # Desteklenmiyorsa tek tek al
        snaps = [db.collection("users").document(uid).get() for uid in uniq]

    for s in snaps:
        if s and s.exists:
            mapping[s.id] = _pick_name(s.to_dict() or {})

    # 2) Eksik kalanlar için Firebase Auth batch
    missing = [uid for uid in uniq if not mapping.get(uid)]
    if missing:
        try:
            resp = fb_auth.get_users([fb_auth.UidIdentifier(uid) for uid in missing])
            for u in resp.users:
                if u and u.uid and (u.display_name or "").strip():
                    mapping[u.uid] = u.display_name
        except Exception:
            # Auth'a erişilemezse sessizce geç
            pass

    return mapping

def _attach_user_names(rows: List[Dict]) -> List[Dict]:
    uids = [r.get("user_id") for r in rows if r.get("user_id")]
    name_map = _load_user_names(uids)
    for r in rows:
        r["user_name"] = name_map.get(r.get("user_id"))
    return rows

def _make_comment(*, user_id: str, target_type: TargetType, target_id: str, content: str, rating: int) -> Dict:
    if _profanity_blocked(content):
        raise HTTPException(status_code=400, detail="Uygunsuz içerik tespit edildi.")
    ref = db.collection("comments").document()
    ref.set({
        "target_type": target_type,
        "target_id": target_id,
        "user_id": user_id,
        "content": content,
        "rating": int(rating),
        "is_deleted": False,
        "is_hidden": False,
        "created_at": firestore.SERVER_TIMESTAMP,
    })
    out = _doc_to_out(ref.get())
    # Create yanıtında da user_name gönder
    out["user_name"] = _load_user_names([user_id]).get(user_id)
    return out

def _list_by_target(*, target_type: TargetType, target_id: str, limit: int) -> List[Dict]:
    q = (
        db.collection("comments")
        .where(filter=FieldFilter("is_deleted", "==", False))
        .where(filter=FieldFilter("is_hidden", "==", False))
        .where(filter=FieldFilter("target_type", "==", target_type))
        .where(filter=FieldFilter("target_id", "==", target_id))
    )
    try:
        q = q.order_by("created_at", direction=gcf.Query.DESCENDING)  # yeni→eski
    except Exception:
        pass
    docs = list(q.limit(limit).stream())
    rows = [_doc_to_out(d) for d in docs]
    return _attach_user_names(rows)

# ---------- Kullanıcı Uçları ----------

@router.post("/products/{product_id}", response_model=CommentOut, summary="Ürüne yorum ekle")
def create_comment_for_product(
    product_id: str,
    content: constr(min_length=1, max_length=500) = Form(...),
    rating: conint(ge=1, le=5) = Form(...),
    current_user: dict = Depends(get_current_user),
):
    return _make_comment(
        user_id=current_user["id"],
        target_type="product",
        target_id=product_id,
        content=content,
        rating=rating,
    )

@router.post("/services/{service_id}", response_model=CommentOut, summary="Hizmete yorum ekle")
def create_comment_for_service(
    service_id: str,
    content: constr(min_length=1, max_length=500) = Form(...),
    rating: conint(ge=1, le=5) = Form(...),
    current_user: dict = Depends(get_current_user),
):
    return _make_comment(
        user_id=current_user["id"],
        target_type="service",
        target_id=service_id,
        content=content,
        rating=rating,
    )

@router.get("/products/{product_id}", response_model=List[CommentOut], summary="Ürün yorumlarını listele (yeni→eski)")
def list_product_comments(
    product_id: str,
    limit: int = Query(100, ge=1, le=200),
):
    return _list_by_target(target_type="product", target_id=product_id, limit=limit)

@router.get("/services/{service_id}", response_model=List[CommentOut], summary="Hizmet yorumlarını listele (yeni→eski)")
def list_service_comments(
    service_id: str,
    limit: int = Query(100, ge=1, le=200),
):
    return _list_by_target(target_type="service", target_id=service_id, limit=limit)

# ---------- Admin Uçları ----------

# (Admin) ÜRÜN yorumları — yeni→eski
@admin_router.get("/", response_model=List[CommentOut], summary="(Admin) Tüm yorumları listele")
def list_all_comments():
    """
    Admin - List all comments
    """
    comments_ref = db.collection("comments").order_by("created_at", direction=firestore.Query.DESCENDING)
    docs = comments_ref.stream()
    comments = []
    for doc in docs:
        comment_data = doc.to_dict()
        comment_data["id"] = doc.id
        comments.append(CommentOut(**comment_data))
    return comments

@admin_router.get("", response_model=List[CommentOut], summary="(Admin) Tüm yorumları listele (no slash)")
def list_all_comments_no_slash():
    """
    Admin - List all comments (no trailing slash)
    """
    return list_all_comments()

@admin_router.get("/products", response_model=List[CommentOut], summary="(Admin) Ürün yorumları (yeni→eski)")
def admin_list_product_comments(
    limit: int = Query(100, ge=1, le=200),
):
    q = (
        db.collection("comments")
        .where(filter=FieldFilter("is_deleted", "==", False))
        .where(filter=FieldFilter("target_type", "==", "product"))
    )
    try:
        q = q.order_by("created_at", direction=gcf.Query.DESCENDING)
    except Exception:
        pass
    docs = list(q.limit(limit).stream())
    rows = [_doc_to_out(d) for d in docs]
    return _attach_user_names(rows)


# (Admin) HİZMET yorumları — yeni→eski
@admin_router.get("/services", response_model=List[CommentOut], summary="(Admin) Hizmet yorumları (yeni→eski)")
def admin_list_service_comments(
    limit: int = Query(100, ge=1, le=200),
):
    q = (
        db.collection("comments")
        .where(filter=FieldFilter("is_deleted", "==", False))
        .where(filter=FieldFilter("target_type", "==", "service"))
    )
    try:
        q = q.order_by("created_at", direction=gcf.Query.DESCENDING)
    except Exception:
        pass
    docs = list(q.limit(limit).stream())
    rows = [_doc_to_out(d) for d in docs]
    return _attach_user_names(rows)


@admin_router.delete("/{comment_id}", summary="(Admin) Sil (soft/hard)")
def admin_delete_comment(comment_id: str, hard: bool = Query(False)):
    ref = db.collection("comments").document(comment_id)
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Comment not found")
    if hard:
        ref.delete()
        return {"detail": "Comment hard deleted"}
    ref.update({"is_deleted": True})
    return {"detail": "Comment deleted"}

@admin_router.put("/{comment_id}/approve", summary="(Admin) Yorumu onayla")
def admin_approve_comment(comment_id: str):
    """
    Admin - Approve a comment (set is_hidden to False and is_deleted to False)
    """
    ref = db.collection("comments").document(comment_id)
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Comment not found")
    
    # Hem is_hidden hem de is_deleted'i false yap
    ref.update({
        "is_hidden": False,
        "is_deleted": False
    })
    return {"detail": "Comment approved"}


def _prof_parent():
    # Ana doküman
    return db.collection("settings").document("profanity")

def _prof_words_col():
    # Alt koleksiyon: settings/profanity/words
    return _prof_parent().collection("words")

def _normalize_words(words: List[str]) -> List[str]:
    seen, out = set(), []
    for w in words or []:
        w2 = (w or "").strip().lower()
        if w2 and w2 not in seen:
            seen.add(w2)
            out.append(w2)
    return out

def _ts_to_dt(ts):
    return ts.to_datetime() if hasattr(ts, "to_datetime") else ts

@admin_router.get("/profanity", summary="(Admin) Küfür kelimeleri - listele (ID ile)")
def admin_list_profanity():
    """
    Döner:
    {
      "items": [
        {"id": "...", "word": "kufur1", "created_at": "..."},
        ...
      ],
      "blocked_words": ["kufur1", "kufur2", ...]  # hızlı kontrol için paralel dizi
    }
    """
    # words alt koleksiyonu
    try:
        q = _prof_words_col().order_by("created_at", direction=gcf.Query.DESCENDING)
        snaps = list(q.stream())
    except Exception:
        snaps = list(_prof_words_col().stream())

    items = []
    for s in snaps:
        d = s.to_dict() or {}
        items.append({
            "id": s.id,
            "word": d.get("word"),
            "created_at": _ts_to_dt(d.get("created_at")),
        })

    # paralel dizi (mevcut kontrol bu diziyi kullanıyor)
    p_snap = _prof_parent().get()
    arr = (p_snap.to_dict() or {}).get("blocked_words", []) if p_snap.exists else []

    return {"items": items, "blocked_words": arr}

@admin_router.post("/profanity", summary="(Admin) Küfür kelimeleri - ekle (ID'li)")
def admin_add_profanity(payload: ProfanityWordsIn):
    """
    Body JSON:
    { "words": ["KÜFÜR1", "  kufur2  "] }

    - Her kelime alt koleksiyonda ayrı bir **doküman** olarak saklanır (auto ID)
    - `settings/profanity.blocked_words` listesi de **ArrayUnion** ile güncellenir
    - Dönüş: güncel liste (admin_list_profanity ile aynı format)
    """
    words = _normalize_words(payload.words)
    if not words:
        return admin_list_profanity()

    parent = _prof_parent()
    col = _prof_words_col()

    # Var olanları bul: hem hızlı dizi, hem alt koleksiyon
    p_snap = parent.get()
    existing_arr = set((p_snap.to_dict() or {}).get("blocked_words", [])) if p_snap.exists else set()

    # Alt koleksiyondaki mevcutları set'e ekleyelim (tekil tutmak için)
    try:
        existing_docs = list(col.stream())
        for s in existing_docs:
            d = s.to_dict() or {}
            if d.get("word"):
                existing_arr.add(d["word"])
    except Exception:
        pass

    to_add = [w for w in words if w not in existing_arr]
    for w in to_add:
        col.document().set({
            "word": w,
            "created_at": firestore.SERVER_TIMESTAMP,
        })

    if to_add:
        parent.set({"blocked_words": gcf.ArrayUnion(to_add)}, merge=True)

    return admin_list_profanity()

@admin_router.delete("/profanity/{word_id}", summary="(Admin) Küfür kelimesi - ID ile sil")
def admin_delete_profanity_by_id(word_id: str):
    """
    - `settings/profanity/words/{word_id}` dokümanını siler
    - Aynı kelimeyi `settings/profanity.blocked_words` dizisinden de kaldırır
    - Dönüş: güncel liste (admin_list_profanity ile aynı format)
    """
    col = _prof_words_col()
    ref = col.document(word_id)
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Word not found")

    data = snap.to_dict() or {}
    word = (data.get("word") or "").strip().lower()

    # Dokümanı sil
    ref.delete()

    # Paralel diziden çıkar
    if word:
        _prof_parent().set({"blocked_words": gcf.ArrayRemove([word])}, merge=True)

    return admin_list_profanity()