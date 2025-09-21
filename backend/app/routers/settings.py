"""
Settings router for admin settings management
"""
from fastapi import APIRouter, Depends, HTTPException
from backend.app.core.auth import get_current_admin
from backend.app.config import db
from typing import Dict, Any
from pydantic import BaseModel
from datetime import datetime

router = APIRouter(prefix="/settings", tags=["Admin: Settings"], dependencies=[Depends(get_current_admin)])

@router.get("/")
def get_settings_data():
    """
    Get basic settings data
    """
    return {"message": "Settings management available", "endpoints": ["email-templates"]}

@router.get("")
def get_settings_data_no_slash():
    """
    Get basic settings data (no trailing slash)
    """
    return get_settings_data()

class EmailTemplate(BaseModel):
    id: str
    name: str
    subject: str
    content: str
    is_active: bool = True
    created_at: datetime = None
    updated_at: datetime = None

class AppSettings(BaseModel):
    site_name: str = "ICS App"
    site_description: str = "E-commerce and Service Booking Platform"
    contact_email: str = "info@icsapp.com"
    contact_phone: str = "+90 555 123 4567"
    address: str = "Istanbul, Turkey"
    currency: str = "TRY"
    timezone: str = "Europe/Istanbul"
    maintenance_mode: bool = False
    allow_registration: bool = True
    require_email_verification: bool = True

@router.get("/")
def get_app_settings():
    """
    Get application settings
    """
    try:
        settings_ref = db.collection("app_settings").document("main")
        doc = settings_ref.get()
        
        if doc.exists:
            return doc.to_dict()
        else:
            # Return default settings if not found
            default_settings = AppSettings().dict()
            settings_ref.set(default_settings)
            return default_settings
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching settings: {str(e)}")

@router.put("/")
def update_app_settings(settings: AppSettings):
    """
    Update application settings
    """
    try:
        settings_data = settings.dict()
        settings_data["updated_at"] = datetime.now()
        
        settings_ref = db.collection("app_settings").document("main")
        settings_ref.set(settings_data, merge=True)
        
        return {"message": "Settings updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating settings: {str(e)}")

@router.get("/email-templates")
def get_email_templates():
    """
    Get all email templates
    """
    try:
        templates_ref = db.collection("email_templates")
        docs = templates_ref.stream()
        
        templates = []
        for doc in docs:
            template_data = doc.to_dict()
            template_data["id"] = doc.id
            templates.append(template_data)
        
        return templates
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching email templates: {str(e)}")

@router.post("/email-templates")
def create_email_template(template: EmailTemplate):
    """
    Create a new email template
    """
    try:
        template_data = template.dict()
        template_data["created_at"] = datetime.now()
        template_data["updated_at"] = datetime.now()
        
        # Remove id from data since Firestore will generate it
        if "id" in template_data:
            del template_data["id"]
        
        doc_ref = db.collection("email_templates").document()
        doc_ref.set(template_data)
        
        return {"id": doc_ref.id, "message": "Email template created successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating email template: {str(e)}")

@router.put("/email-templates/{template_id}")
def update_email_template(template_id: str, template: EmailTemplate):
    """
    Update an email template
    """
    try:
        template_data = template.dict()
        template_data["updated_at"] = datetime.now()
        
        # Remove id from data
        if "id" in template_data:
            del template_data["id"]
        
        doc_ref = db.collection("email_templates").document(template_id)
        doc_ref.update(template_data)
        
        return {"message": "Email template updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating email template: {str(e)}")

@router.delete("/email-templates/{template_id}")
def delete_email_template(template_id: str):
    """
    Delete an email template
    """
    try:
        doc_ref = db.collection("email_templates").document(template_id)
        doc_ref.delete()
        
        return {"message": "Email template deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting email template: {str(e)}")

@router.get("/backup")
def get_backup_settings():
    """
    Get backup settings
    """
    try:
        backup_ref = db.collection("backup_settings").document("main")
        doc = backup_ref.get()
        
        if doc.exists:
            return doc.to_dict()
        else:
            return {
                "auto_backup": False,
                "backup_frequency": "daily",
                "backup_retention_days": 30,
                "last_backup": None
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching backup settings: {str(e)}")

@router.put("/backup")
def update_backup_settings(backup_settings: Dict[str, Any]):
    """
    Update backup settings
    """
    try:
        backup_settings["updated_at"] = datetime.now()
        
        backup_ref = db.collection("backup_settings").document("main")
        backup_ref.set(backup_settings, merge=True)
        
        return {"message": "Backup settings updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating backup settings: {str(e)}")
