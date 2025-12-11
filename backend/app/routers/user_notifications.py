"""
User notifications router for user notification management
"""
from fastapi import APIRouter, Depends, HTTPException
from backend.app.core.security import get_current_user
from backend.app.config import db
from typing import List, Dict, Any
from datetime import datetime
from firebase_admin import firestore
from backend.app.schemas.notification import UserNotificationOut

router = APIRouter(prefix="/notifications", tags=["User: Notifications"])

@router.get("/", response_model=List[UserNotificationOut])
def get_notifications(current_user: dict = Depends(get_current_user)):
    """
    Get all notifications for the current user
    """
    try:
        user_id = current_user.get("uid")
        if not user_id:
            raise HTTPException(status_code=400, detail="User ID not found")
        
        # Get notifications for this user, ordered by created_at descending
        notifications_ref = db.collection("user_notifications")
        query = notifications_ref.where("user_id", "==", user_id).order_by("created_at", direction=firestore.Query.DESCENDING)
        docs = query.stream()
        
        notifications = []
        for doc in docs:
            notification_data = doc.to_dict()
            notification_data["id"] = doc.id
            notifications.append(UserNotificationOut(**notification_data))
        
        return notifications
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching notifications: {str(e)}")

@router.get("/{notification_id}", response_model=UserNotificationOut)
def get_notification(notification_id: str, current_user: dict = Depends(get_current_user)):
    """
    Get a specific notification by ID
    """
    try:
        user_id = current_user.get("uid")
        if not user_id:
            raise HTTPException(status_code=400, detail="User ID not found")
        
        doc_ref = db.collection("user_notifications").document(notification_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            raise HTTPException(status_code=404, detail="Notification not found")
        
        notification_data = doc.to_dict()
        if notification_data.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="Not authorized to access this notification")
        
        notification_data["id"] = doc.id
        return UserNotificationOut(**notification_data)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching notification: {str(e)}")

@router.put("/{notification_id}/read", response_model=Dict[str, str])
def mark_notification_as_read(notification_id: str, current_user: dict = Depends(get_current_user)):
    """
    Mark a notification as read
    """
    try:
        user_id = current_user.get("uid")
        if not user_id:
            raise HTTPException(status_code=400, detail="User ID not found")
        
        doc_ref = db.collection("user_notifications").document(notification_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            raise HTTPException(status_code=404, detail="Notification not found")
        
        notification_data = doc.to_dict()
        if notification_data.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="Not authorized to access this notification")
        
        doc_ref.update({
            "is_read": True,
            "read_at": firestore.SERVER_TIMESTAMP
        })
        
        return {"message": "Notification marked as read"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error marking notification as read: {str(e)}")

@router.put("/read-all", response_model=Dict[str, str])
def mark_all_notifications_as_read(current_user: dict = Depends(get_current_user)):
    """
    Mark all notifications as read for the current user
    """
    try:
        user_id = current_user.get("uid")
        if not user_id:
            raise HTTPException(status_code=400, detail="User ID not found")
        
        # Get all unread notifications for this user
        notifications_ref = db.collection("user_notifications")
        query = notifications_ref.where("user_id", "==", user_id).where("is_read", "==", False)
        docs = query.stream()
        
        batch = db.batch()
        count = 0
        for doc in docs:
            doc_ref = db.collection("user_notifications").document(doc.id)
            batch.update(doc_ref, {
                "is_read": True,
                "read_at": firestore.SERVER_TIMESTAMP
            })
            count += 1
        
        if count > 0:
            batch.commit()
        
        return {"message": f"{count} notifications marked as read"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error marking notifications as read: {str(e)}")

@router.delete("/{notification_id}", response_model=Dict[str, str])
def delete_notification(notification_id: str, current_user: dict = Depends(get_current_user)):
    """
    Delete a notification
    """
    try:
        user_id = current_user.get("uid")
        if not user_id:
            raise HTTPException(status_code=400, detail="User ID not found")
        
        doc_ref = db.collection("user_notifications").document(notification_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            raise HTTPException(status_code=404, detail="Notification not found")
        
        notification_data = doc.to_dict()
        if notification_data.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="Not authorized to delete this notification")
        
        doc_ref.delete()
        
        return {"message": "Notification deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting notification: {str(e)}")
