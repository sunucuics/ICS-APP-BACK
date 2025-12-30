"""
Notifications router for admin notification management
"""
from fastapi import APIRouter, Depends, HTTPException
from backend.app.core.security import get_current_admin
from backend.app.config import db
from typing import List, Dict, Any
from pydantic import BaseModel
from datetime import datetime
import firebase_admin
from firebase_admin import messaging
import os
from backend.app.schemas.notification import (
    NotificationTemplateBase,
    NotificationTemplateOut,
    NotificationSendRequest
)
from firebase_admin import firestore

router = APIRouter(prefix="/notifications", tags=["Admin: Notifications"], dependencies=[Depends(get_current_admin)])

@router.get("/")
def get_notifications_data():
    """
    Get basic notifications data
    """
    return {"message": "Notifications management available", "endpoints": ["templates", "campaigns"]}

@router.get("")
def get_notifications_data_no_slash():
    """
    Get basic notifications data (no trailing slash)
    """
    return get_notifications_data()

class NotificationTemplate(BaseModel):
    id: str
    name: str
    subject: str
    content: str
    type: str  # email, sms, push
    is_active: bool = True
    created_at: datetime = None
    updated_at: datetime = None

class NotificationCampaign(BaseModel):
    id: str
    name: str
    template_id: str
    target_audience: str  # all, specific_users, etc.
    status: str  # draft, scheduled, sent
    scheduled_at: datetime = None
    sent_at: datetime = None
    created_at: datetime = None

@router.get("/templates")
def get_notification_templates():
    try:
        docs = db.collection("notification_templates").stream()
        out = []
        for d in docs:
            raw = d.to_dict() or {}
            # backward compat: migrate "content" -> "body"
            if "body" not in raw and "content" in raw:
                raw["body"] = raw["content"]
            
            # Validate and normalize type field
            template_type = raw.get("type", "email")
            # Only allow valid types: email, sms, push
            if template_type not in ["email", "sms", "push"]:
                template_type = "email"  # Default to email for invalid types
            
            out.append(NotificationTemplateOut(
                id=d.id,
                name=raw.get("name",""),
                subject=raw.get("subject"),
                body=raw.get("body",""),
                type=template_type,
                is_active=bool(raw.get("is_active", True)),
                created_at=raw.get("created_at"),
                updated_at=raw.get("updated_at"),
            ).dict())
        return out
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching templates: {str(e)}")

@router.post("/templates")
def create_notification_template(template: NotificationTemplateBase):
    try:
        data = {
            "name": template.name,
            "subject": template.subject,
            "body": template.body,
            "type": template.type,
            "is_active": template.is_active,
            "created_at": firestore.SERVER_TIMESTAMP,
            "updated_at": firestore.SERVER_TIMESTAMP,
        }
        ref = db.collection("notification_templates").document()
        ref.set(data)

        return {"id": ref.id, "message": "Template created successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating template: {str(e)}")

@router.put("/templates/{template_id}")
def update_notification_template(template_id: str, template: NotificationTemplateBase):
    try:
        data = {
            "name": template.name,
            "subject": template.subject,
            "body": template.body,
            "type": template.type,
            "is_active": template.is_active,
            "updated_at": datetime.now(),
        }
        db.collection("notification_templates").document(template_id).update(data)

        return {"message": "Template updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating template: {str(e)}")

@router.delete("/templates/{template_id}")
def delete_notification_template(template_id: str):
    """
    Delete a notification template
    """
    try:
        doc_ref = db.collection("notification_templates").document(template_id)
        doc_ref.delete()
        
        return {"message": "Template deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting template: {str(e)}")

@router.get("/campaigns")
def get_notification_campaigns():
    """
    Get all notification campaigns
    """
    try:
        campaigns_ref = db.collection("notification_campaigns")
        docs = campaigns_ref.stream()
        
        campaigns = []
        for doc in docs:
            campaign_data = doc.to_dict()
            campaign_data["id"] = doc.id
            campaigns.append(campaign_data)
        
        return campaigns
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching campaigns: {str(e)}")

@router.post("/campaigns")
def create_notification_campaign(campaign: NotificationCampaign):
    """
    Create a new notification campaign
    """
    try:
        campaign_data = campaign.dict()
        if "id" in campaign_data:
            del campaign_data["id"]

        campaign_data["created_at"] = firestore.SERVER_TIMESTAMP

        doc_ref = db.collection("notification_campaigns").document()
        doc_ref.set(campaign_data)

        return {"id": doc_ref.id, "message": "Campaign created successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating campaign: {str(e)}")

@router.put("/campaigns/{campaign_id}")
def update_notification_campaign(campaign_id: str, campaign: NotificationCampaign):
    """
    Update a notification campaign
    """
    try:
        campaign_data = campaign.dict()
        if "id" in campaign_data:
            del campaign_data["id"]

        campaign_data["updated_at"] = firestore.SERVER_TIMESTAMP

        db.collection("notification_campaigns").document(campaign_id).update(campaign_data)

        return {"message": "Campaign updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating campaign: {str(e)}")

@router.delete("/campaigns/{campaign_id}")
def delete_notification_campaign(campaign_id: str):
    """
    Delete a notification campaign
    """
    try:
        doc_ref = db.collection("notification_campaigns").document(campaign_id)
        doc_ref.delete()
        
        return {"message": "Campaign deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting campaign: {str(e)}")

@router.post("/send")
def send_notification(notification_data: NotificationSendRequest):
    """
    Send push notification to users and save to database
    """
    try:
        title = notification_data.title
        body = notification_data.body
        # Support both 'segments' and 'user_segments' for backward compatibility
        target_segments = notification_data.segments or notification_data.user_segments or []
        template_id = notification_data.template_id
        
        # Get user FCM tokens based on segments
        users_ref = db.collection("users")
        
        # If specific target users are provided, filter by them
        if notification_data.target_users:
            user_ids = notification_data.target_users
            fcm_tokens = []
            user_tokens_map = {}
            
            # Fetch specific users
            for user_id in user_ids:
                try:
                    user_doc = users_ref.document(user_id).get()
                    if user_doc.exists:
                        user_data = user_doc.to_dict()
                        if "fcm_token" in user_data and user_data["fcm_token"]:
                            fcm_token = user_data["fcm_token"]
                            fcm_tokens.append(fcm_token)
                            user_tokens_map[user_id] = fcm_token
                except Exception as e:
                    # Skip invalid user IDs
                    continue
        else:
            # Get all users or filter by segments
            if not target_segments:  # Send to all users
                users = users_ref.stream()
            else:
                # Filter users by segments (this would need more complex logic)
                # For now, send to all users if segments are specified
                users = users_ref.stream()
            
            fcm_tokens = []
            user_ids = []
            user_tokens_map = {}  # Map user_id to fcm_token
            
            # Collect all users (with or without FCM tokens)
            for user_doc in users:
                try:
                    user_data = user_doc.to_dict()
                    if not user_data:
                        continue
                    user_id = user_doc.id
                    user_ids.append(user_id)
                    
                    # Collect FCM tokens for push notifications
                    if "fcm_token" in user_data and user_data["fcm_token"]:
                        fcm_token = user_data["fcm_token"]
                        if fcm_token:  # Ensure token is not empty
                            fcm_tokens.append(fcm_token)
                            user_tokens_map[user_id] = fcm_token
                except Exception as e:
                    # Skip invalid user documents
                    continue
        
        # Validate we have at least title and body
        if not title or not body:
            raise HTTPException(status_code=400, detail="Title and body are required")
        
        # Send FCM notification (only if we have tokens)
        success_count = 0
        failure_count = 0
        
        # Save notifications to database for all users
        notifications_ref = db.collection("user_notifications")
        batch = db.batch()
        batch_count = 0
        
        # Prepare notification data
        notification_data_db_base = {
            "title": title,
            "body": body,
            "type": "admin_notification",
            "is_read": False,
            "created_at": firestore.SERVER_TIMESTAMP,
        }
        
        # Add optional data
        if template_id or target_segments:
            notification_data_db_base["data"] = {
                "template_id": template_id,
                "segments": target_segments
            }
        
        # Save notifications to database
        for user_id in user_ids:
            try:
                notification_doc = notifications_ref.document()
                notification_data_db = {
                    **notification_data_db_base,
                    "user_id": user_id
                }
                batch.set(notification_doc, notification_data_db)
                batch_count += 1
                
                # Firestore batch limit is 500, commit when approaching limit
                if batch_count >= 450:
                    batch.commit()
                    batch = db.batch()
                    batch_count = 0
            except Exception as e:
                # Continue with other users if one fails
                continue
        
        # Commit remaining notifications
        if batch_count > 0:
            batch.commit()
        
        # Send FCM push notifications if we have tokens
        if fcm_tokens:
            try:
                # Create the message with high priority for delivery when device is off
                # Split tokens into batches of 500 (FCM limit)
                batch_size = 500
                total_success = 0
                total_failure = 0
                
                for i in range(0, len(fcm_tokens), batch_size):
                    batch_tokens = fcm_tokens[i:i + batch_size]
                    
                    multicast_message = messaging.MulticastMessage(
                        notification=messaging.Notification(
                            title=title,
                            body=body,
                        ),
                        data={
                            'click_action': 'FLUTTER_NOTIFICATION_CLICK',
                            'type': 'admin_notification',
                            'title': title,
                            'body': body,
                        },
                        tokens=batch_tokens,
                        android=messaging.AndroidConfig(
                            priority='high',
                            notification=messaging.AndroidNotification(
                                priority='high',
                                sound='default',
                                channel_id='high_importance_channel',
                            ),
                        ),
                        apns=messaging.APNSConfig(
                            headers={
                                'apns-priority': '10',  # 10 = immediate delivery (required for alerts)
                                'apns-push-type': 'alert',
                            },
                            payload=messaging.APNSPayload(
                                aps=messaging.Aps(
                                    alert=messaging.ApsAlert(
                                        title=title,
                                        body=body,
                                    ),
                                    sound='default',
                                    badge=1,
                                    mutable_content=True,
                                ),
                            ),
                        ),
                    )
                    
                    # Use send_each_for_multicast instead of deprecated send_multicast
                    response = messaging.send_each_for_multicast(multicast_message)
                    total_success += response.success_count
                    total_failure += response.failure_count
                
                success_count = total_success
                failure_count = total_failure
            except Exception as e:
                # Log FCM error but don't fail the entire request
                # Notifications are already saved to database
                print(f"FCM send error: {str(e)}")
                failure_count = len(fcm_tokens)
        
        return {
            "message": "Notification sent successfully",
            "target_count": len(user_ids),
            "fcm_token_count": len(fcm_tokens),
            "success_count": success_count,
            "failure_count": failure_count,
            "saved_to_db": len(user_ids),
            "title": title,
            "body": body
        }
        
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"Error sending notification: {str(e)}\n{error_trace}")
        raise HTTPException(status_code=500, detail=f"Error sending notification: {str(e)}")
