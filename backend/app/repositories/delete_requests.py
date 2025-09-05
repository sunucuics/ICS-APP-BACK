import time
from typing import Optional, Dict, Any
from app.config import db
from google.cloud import firestore as gcf

COL = "delete_requests"

def now_ts() -> int:
    return int(time.time())

def get(uid: str) -> Optional[Dict[str, Any]]:
    doc = db.collection(COL).document(uid).get()
    return doc.to_dict() if doc.exists else None

def create_or_replace(uid: str, code_hash: str, ttl_seconds: int) -> None:
    db.collection(COL).document(uid).set({
        "uid": uid,
        "code_hash": code_hash,
        "requested_at": gcf.SERVER_TIMESTAMP,
        "expires_at_unix": now_ts() + ttl_seconds,
        "consumed": False,
        "attempts": 0,
        "last_sent_at_unix": now_ts(),
    })

def increment_attempt(uid: str) -> None:
    db.collection(COL).document(uid).update({"attempts": gcf.Increment(1)})

def consume(uid: str) -> None:
    db.collection(COL).document(uid).update({
        "consumed": True,
        "consumed_at": gcf.SERVER_TIMESTAMP
    })
