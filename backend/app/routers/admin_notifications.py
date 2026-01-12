"""
Admin Notifications Router
Admin panele gelen bildirimleri yönetmek için API endpoint'leri
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import List, Optional
from datetime import datetime

from backend.app.core.security import get_current_admin
from backend.app.config import db
from backend.app.schemas.admin_notification import (
    AdminNotificationOut,
    UnreadCountResponse
)
from firebase_admin import firestore

router = APIRouter(prefix="/notifications", tags=["Admin: Notifications Panel"])


def _parse_datetime(value) -> Optional[datetime]:
    """Firestore Timestamp veya string'i datetime'a çevirir"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if hasattr(value, "to_datetime"):
        return value.to_datetime()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None



@router.get("", response_model=List[AdminNotificationOut])
@router.get("/", response_model=List[AdminNotificationOut])
def get_admin_notifications(
    limit: int = 50,
    unread_only: bool = False,
    _: dict = Depends(get_current_admin)
):
    """
    Admin panele ait tüm bildirimleri getirir.
    - **limit**: Maksimum bildirim sayısı (varsayılan: 50)
    - **unread_only**: Sadece okunmamış bildirimleri getir
    """
    try:
        query = db.collection("admin_notifications")
        
        if unread_only:
            query = query.where("is_read", "==", False)
        
        # En yeniden en eskiye sırala
        query = query.order_by("created_at", direction=firestore.Query.DESCENDING)
        query = query.limit(limit)
        
        docs = query.stream()
        
        notifications = []
        for doc in docs:
            data = doc.to_dict() or {}
            notifications.append(AdminNotificationOut(
                id=doc.id,
                title=data.get("title", ""),
                body=data.get("body", ""),
                type=data.get("type", "system"),
                is_read=data.get("is_read", False),
                created_at=_parse_datetime(data.get("created_at")),
                read_at=_parse_datetime(data.get("read_at")),
                data=data.get("data")
            ))
        
        return notifications
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching notifications: {str(e)}")


@router.get("/unread-count", response_model=UnreadCountResponse)
def get_unread_count(_: dict = Depends(get_current_admin)):
    """
    Okunmamış admin bildirim sayısını döndürür.
    """
    try:
        query = db.collection("admin_notifications").where("is_read", "==", False)
        docs = list(query.stream())
        return UnreadCountResponse(count=len(docs))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching unread count: {str(e)}")


@router.put("/{notification_id}/read")
def mark_notification_as_read(
    notification_id: str,
    _: dict = Depends(get_current_admin)
):
    """
    Belirtilen bildirimi okundu olarak işaretler.
    """
    try:
        doc_ref = db.collection("admin_notifications").document(notification_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            raise HTTPException(status_code=404, detail="Notification not found")
        
        doc_ref.update({
            "is_read": True,
            "read_at": firestore.SERVER_TIMESTAMP
        })
        
        return {"message": "Notification marked as read"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error marking notification as read: {str(e)}")


@router.put("/read-all")
def mark_all_notifications_as_read(_: dict = Depends(get_current_admin)):
    """
    Tüm okunmamış bildirimleri okundu olarak işaretler.
    """
    try:
        query = db.collection("admin_notifications").where("is_read", "==", False)
        docs = list(query.stream())
        
        if not docs:
            return {"message": "No unread notifications", "count": 0}
        
        batch = db.batch()
        for doc in docs:
            doc_ref = db.collection("admin_notifications").document(doc.id)
            batch.update(doc_ref, {
                "is_read": True,
                "read_at": firestore.SERVER_TIMESTAMP
            })
        
        batch.commit()
        
        return {"message": f"{len(docs)} notifications marked as read", "count": len(docs)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error marking notifications as read: {str(e)}")


@router.delete("/{notification_id}")
def delete_notification(
    notification_id: str,
    _: dict = Depends(get_current_admin)
):
    """
    Belirtilen bildirimi siler.
    """
    try:
        doc_ref = db.collection("admin_notifications").document(notification_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            raise HTTPException(status_code=404, detail="Notification not found")
        
        doc_ref.delete()
        
        return {"message": "Notification deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting notification: {str(e)}")


@router.delete("/clear-all")
def clear_all_notifications(_: dict = Depends(get_current_admin)):
    """
    Tüm okunmuş bildirimleri siler.
    """
    try:
        query = db.collection("admin_notifications").where("is_read", "==", True)
        docs = list(query.stream())
        
        if not docs:
            return {"message": "No read notifications to clear", "count": 0}
        
        batch = db.batch()
        for doc in docs:
            doc_ref = db.collection("admin_notifications").document(doc.id)
            batch.delete(doc_ref)
        
        batch.commit()
        
        return {"message": f"{len(docs)} notifications cleared", "count": len(docs)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error clearing notifications: {str(e)}")
