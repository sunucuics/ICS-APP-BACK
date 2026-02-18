"""
Admin Notifications Router
Admin panele gelen bildirimleri yönetmek için API endpoint'leri
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from typing import List, Optional
from datetime import datetime
import asyncio
import json
import logging

from backend.app.core.security import get_current_admin
from backend.app.config import db
from backend.app.schemas.admin_notification import (
    AdminNotificationOut,
    UnreadCountResponse
)
from firebase_admin import firestore

logger = logging.getLogger("ics.admin_notifications")

router = APIRouter(prefix="/notifications", tags=["Admin: Notifications Panel"])


def _parse_datetime(value) -> Optional[datetime]:
    """Firestore Timestamp veya string'i datetime'a çevirir (naive datetime döndürür)"""
    if value is None:
        return None
    if isinstance(value, datetime):
        # Convert to naive if timezone-aware
        return value.replace(tzinfo=None) if value.tzinfo else value
    if hasattr(value, "to_datetime"):
        dt = value.to_datetime()
        # Convert to naive if timezone-aware
        return dt.replace(tzinfo=None) if dt.tzinfo else dt
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            # Convert to naive if timezone-aware
            return dt.replace(tzinfo=None) if dt.tzinfo else dt
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


@router.get("/stream")
async def stream_admin_notifications(_: dict = Depends(get_current_admin)):
    """
    Server-Sent Events (SSE) endpoint for real-time admin notifications.
    Uses asyncio.Queue to bridge Firestore's threaded listener with the
    async event loop — no blocking calls on the event loop.
    """
    async def event_generator():
        loop = asyncio.get_event_loop()
        aqueue: asyncio.Queue = asyncio.Queue()
        is_first_snapshot = True
        existing_notification_ids: set = set()
        connection_start_time = None
        listener = None

        def on_snapshot(doc_snapshot, changes, read_time):
            """Firestore listener callback — runs in a background thread."""
            nonlocal is_first_snapshot, existing_notification_ids, connection_start_time

            try:
                if is_first_snapshot:
                    for doc in doc_snapshot:
                        existing_notification_ids.add(doc.id)
                    is_first_snapshot = False
                    connection_start_time = datetime.utcnow().replace(tzinfo=None)
                    logger.info(f"SSE: Initial snapshot with {len(existing_notification_ids)} existing notifications")
                    return

                for change in changes:
                    if change.type.name == 'ADDED':
                        doc = change.document
                        if doc.id in existing_notification_ids:
                            continue
                        data = doc.to_dict() or {}
                        if data.get("is_read", False):
                            continue
                        created_at = _parse_datetime(data.get("created_at"))
                        if connection_start_time and created_at and created_at < connection_start_time:
                            existing_notification_ids.add(doc.id)
                            continue
                        created_at_str = created_at.isoformat() if created_at else None
                        notification_data = {
                            "id": doc.id,
                            "title": data.get("title", ""),
                            "body": data.get("body", ""),
                            "type": data.get("type", "system"),
                            "is_read": False,
                            "created_at": created_at_str,
                            "data": data.get("data")
                        }
                        loop.call_soon_threadsafe(
                            aqueue.put_nowait,
                            {"type": "notification", "data": notification_data}
                        )
                        logger.info(f"SSE: New notification {doc.id} queued")

                    elif change.type.name == 'MODIFIED':
                        doc = change.document
                        data = doc.to_dict() or {}
                        if data.get("is_read") is True:
                            loop.call_soon_threadsafe(
                                aqueue.put_nowait,
                                {"type": "notification_updated", "data": {"id": doc.id, "is_read": True}}
                            )
            except Exception as e:
                logger.error(f"Error in Firestore listener: {e}")

        # Start Firestore listener
        query = db.collection("admin_notifications")
        listener = query.on_snapshot(on_snapshot)

        try:
            # Connected event
            yield f"data: {json.dumps({'type': 'connected', 'timestamp': datetime.utcnow().isoformat()})}\n\n"

            # Initial unread count — blocking Firestore call runs in thread
            try:
                def _count_unread():
                    count = 0
                    for doc in db.collection("admin_notifications").stream():
                        d = doc.to_dict() or {}
                        if not d.get("is_read", False):
                            count += 1
                    return count

                unread_count = await asyncio.to_thread(_count_unread)
                yield f"data: {json.dumps({'type': 'unread_count', 'count': unread_count})}\n\n"
            except Exception as e:
                logger.error(f"Error getting initial unread count: {e}")
                yield f"data: {json.dumps({'type': 'unread_count', 'count': 0})}\n\n"

            last_heartbeat = datetime.utcnow()
            heartbeat_interval = 30

            while True:
                try:
                    # Non-blocking wait on asyncio.Queue (max 1 sec)
                    try:
                        message = await asyncio.wait_for(aqueue.get(), timeout=1.0)
                        yield f"data: {json.dumps(message)}\n\n"
                    except asyncio.TimeoutError:
                        pass

                    # Heartbeat
                    now = datetime.utcnow()
                    if (now - last_heartbeat).total_seconds() >= heartbeat_interval:
                        yield f"data: {json.dumps({'type': 'heartbeat', 'timestamp': now.isoformat()})}\n\n"
                        last_heartbeat = now

                except asyncio.CancelledError:
                    logger.info("SSE stream cancelled")
                    break
                except Exception as e:
                    logger.error(f"Error in event generator: {e}")
                    yield f"data: {json.dumps({'type': 'error', 'data': {'message': str(e)}})}\n\n"
                    await asyncio.sleep(1)

        finally:
            # Properly unsubscribe the Firestore listener
            if listener is not None:
                try:
                    listener.unsubscribe()
                except Exception as e:
                    logger.error(f"Error unsubscribing listener: {e}")
            logger.info("SSE stream closed, Firestore listener unsubscribed")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )
