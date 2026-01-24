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
import threading
from queue import Queue, Empty

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
def stream_admin_notifications(_: dict = Depends(get_current_admin)):
    """
    Server-Sent Events (SSE) endpoint for real-time admin notifications.
    Streams new notifications as they are created in Firestore.
    """
    async def event_generator():
        """Async generator that yields SSE events"""
        message_queue = Queue()
        listener_stopped = threading.Event()
        is_first_snapshot = True
        existing_notification_ids = set()
        connection_start_time = None  # Will be set after first snapshot
        
        def on_snapshot(doc_snapshot, changes, read_time):
            """Firestore listener callback"""
            nonlocal is_first_snapshot, existing_notification_ids, connection_start_time
            
            try:
                # On first snapshot, collect existing notification IDs to ignore them
                if is_first_snapshot:
                    for doc in doc_snapshot:
                        existing_notification_ids.add(doc.id)
                    is_first_snapshot = False
                    # Use naive datetime for comparison
                    connection_start_time = datetime.utcnow().replace(tzinfo=None)
                    logger.info(f"SSE: Initial snapshot with {len(existing_notification_ids)} existing notifications")
                    return
                
                # Process changes (only new notifications after initial snapshot)
                for change in changes:
                    if change.type.name == 'ADDED':
                        # New notification added (after initial snapshot)
                        doc = change.document
                        
                        # Skip if this was in the initial snapshot
                        if doc.id in existing_notification_ids:
                            continue
                            
                        data = doc.to_dict() or {}
                        
                        # Only send unread notifications
                        if data.get("is_read", False):
                            continue
                        
                        # Parse created_at
                        created_at = _parse_datetime(data.get("created_at"))
                        
                        # Check if notification was created after connection start (safety check)
                        # Skip this check if connection_start_time is not set yet
                        if connection_start_time and created_at:
                            if created_at < connection_start_time:
                                # This notification was created before we connected, skip it
                                existing_notification_ids.add(doc.id)
                                continue
                            
                        # Format created_at for JSON serialization
                        created_at_str = created_at.isoformat() if created_at else None
                        
                        notification_data = {
                            "id": doc.id,
                            "title": data.get("title", ""),
                            "body": data.get("body", ""),
                            "type": data.get("type", "system"),
                            "is_read": data.get("is_read", False),
                            "created_at": created_at_str,
                            "data": data.get("data")
                        }
                        
                        message_queue.put({
                            "type": "notification",
                            "data": notification_data
                        })
                        logger.info(f"SSE: New notification {doc.id} queued - {notification_data.get('title', 'No title')}")
                        
                    elif change.type.name == 'MODIFIED':
                        # Notification updated (e.g., marked as read)
                        doc = change.document
                        data = doc.to_dict() or {}
                        
                        # Only send if it affects unread count
                        if data.get("is_read") == True:
                            message_queue.put({
                                "type": "notification_updated",
                                "data": {
                                    "id": doc.id,
                                    "is_read": True
                                }
                            })
                            logger.info(f"SSE: Notification {doc.id} marked as read")
                            
                            # Recalculate and send updated unread count
                            try:
                                all_notifications = db.collection("admin_notifications").stream()
                                unread_count = 0
                                for notification_doc in all_notifications:
                                    notification_data = notification_doc.to_dict() or {}
                                    if not notification_data.get("is_read", False):
                                        unread_count += 1
                                message_queue.put({
                                    "type": "unread_count",
                                    "count": unread_count
                                })
                                logger.info(f"SSE: Unread count updated to {unread_count}")
                            except Exception as e:
                                logger.error(f"Error calculating unread count after update: {e}")
            except Exception as e:
                logger.error(f"Error in Firestore listener: {e}")
                message_queue.put({
                    "type": "error",
                    "data": {"message": str(e)}
                })
        
        # Set up Firestore listener for new notifications
        # Listen to all notifications (no order_by to avoid index requirement)
        # We'll filter and process in the callback
        query = db.collection("admin_notifications")
        
        # Start listening
        # Note: on_snapshot returns a Watch object that runs in a background thread
        listener = query.on_snapshot(on_snapshot)
        
        try:
            # Send initial heartbeat
            yield f"data: {json.dumps({'type': 'connected', 'timestamp': datetime.utcnow().isoformat()})}\n\n"
            
            # Send initial unread count
            # Count unread notifications manually to avoid index requirement
            try:
                all_notifications = db.collection("admin_notifications").stream()
                unread_count = 0
                for doc in all_notifications:
                    data = doc.to_dict() or {}
                    if not data.get("is_read", False):
                        unread_count += 1
                yield f"data: {json.dumps({'type': 'unread_count', 'count': unread_count})}\n\n"
            except Exception as e:
                logger.error(f"Error getting initial unread count: {e}")
                # Send 0 as fallback
                yield f"data: {json.dumps({'type': 'unread_count', 'count': 0})}\n\n"
            
            # Heartbeat interval (30 seconds)
            last_heartbeat = datetime.utcnow()
            heartbeat_interval = 30
            
            while True:
                try:
                    # Check for messages from Firestore listener (non-blocking)
                    try:
                        message = message_queue.get(timeout=1)
                        yield f"data: {json.dumps(message)}\n\n"
                    except Empty:
                        pass
                    
                    # Send heartbeat periodically
                    now = datetime.utcnow()
                    if (now - last_heartbeat).total_seconds() >= heartbeat_interval:
                        yield f"data: {json.dumps({'type': 'heartbeat', 'timestamp': now.isoformat()})}\n\n"
                        last_heartbeat = now
                    
                    # Small sleep to prevent busy waiting
                    await asyncio.sleep(0.1)
                    
                except asyncio.CancelledError:
                    logger.info("SSE stream cancelled")
                    break
                except Exception as e:
                    logger.error(f"Error in event generator: {e}")
                    yield f"data: {json.dumps({'type': 'error', 'data': {'message': str(e)}})}\n\n"
                    await asyncio.sleep(1)
        
        finally:
            # Clean up: Firestore Watch object doesn't have stop() or unsubscribe()
            # The listener will automatically stop when the connection closes
            # We just need to mark it as stopped
            try:
                # Firestore Watch runs in a background thread and will stop automatically
                # when the connection is closed. No explicit stop needed.
                pass
            except Exception as e:
                logger.error(f"Error in cleanup: {e}")
            listener_stopped.set()
            logger.info("SSE stream closed, Firestore listener will stop automatically")
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"  # Disable nginx buffering
        }
    )
