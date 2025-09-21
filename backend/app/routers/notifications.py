"""
Notifications router for admin notification management
"""
from fastapi import APIRouter, Depends, HTTPException
from backend.app.core.auth import get_current_admin
from backend.app.config import db
from typing import List, Dict, Any
from pydantic import BaseModel
from datetime import datetime
import firebase_admin
from firebase_admin import messaging
import os

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
    """
    Get all notification templates
    """
    try:
        templates_ref = db.collection("notification_templates")
        docs = templates_ref.stream()
        
        templates = []
        for doc in docs:
            template_data = doc.to_dict()
            template_data["id"] = doc.id
            templates.append(template_data)
        
        return templates
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching templates: {str(e)}")

@router.post("/templates")
def create_notification_template(template: NotificationTemplate):
    """
    Create a new notification template
    """
    try:
        template_data = template.dict()
        template_data["created_at"] = datetime.now()
        template_data["updated_at"] = datetime.now()
        
        # Remove id from data since Firestore will generate it
        if "id" in template_data:
            del template_data["id"]
        
        doc_ref = db.collection("notification_templates").document()
        doc_ref.set(template_data)
        
        return {"id": doc_ref.id, "message": "Template created successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating template: {str(e)}")

@router.put("/templates/{template_id}")
def update_notification_template(template_id: str, template: NotificationTemplate):
    """
    Update a notification template
    """
    try:
        template_data = template.dict()
        template_data["updated_at"] = datetime.now()
        
        # Remove id from data
        if "id" in template_data:
            del template_data["id"]
        
        doc_ref = db.collection("notification_templates").document(template_id)
        doc_ref.update(template_data)
        
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
        campaign_data["created_at"] = datetime.now()
        
        # Remove id from data since Firestore will generate it
        if "id" in campaign_data:
            del campaign_data["id"]
        
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
        
        # Remove id from data
        if "id" in campaign_data:
            del campaign_data["id"]
        
        doc_ref = db.collection("notification_campaigns").document(campaign_id)
        doc_ref.update(campaign_data)
        
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
def send_notification(notification_data: dict):
    """
    Send push notification to users
    """
    try:
        title = notification_data.get("title", "")
        body = notification_data.get("body", "")
        target_segments = notification_data.get("segments", [])
        
        # Get user FCM tokens based on segments
        users_ref = db.collection("users")
        
        if not target_segments:  # Send to all users
            users = users_ref.stream()
        else:
            # Filter users by segments (this would need more complex logic)
            users = users_ref.stream()
        
        fcm_tokens = []
        for user_doc in users:
            user_data = user_doc.to_dict()
            if "fcm_token" in user_data and user_data["fcm_token"]:
                fcm_tokens.append(user_data["fcm_token"])
        
        # Send FCM notification
        if fcm_tokens:
            # Create the message
            message = messaging.MulticastMessage(
                notification=messaging.Notification(
                    title=title,
                    body=body,
                ),
                data={
                    'click_action': 'FLUTTER_NOTIFICATION_CLICK',
                    'type': 'admin_notification'
                },
                tokens=fcm_tokens,
            )
            
            # Send the message
            response = messaging.send_multicast(message)
            
            return {
                "message": "Notification sent successfully",
                "target_count": len(fcm_tokens),
                "success_count": response.success_count,
                "failure_count": response.failure_count,
                "title": title,
                "body": body
            }
        else:
            return {
                "message": "No FCM tokens found",
                "target_count": 0,
                "title": title,
                "body": body
            }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error sending notification: {str(e)}")
