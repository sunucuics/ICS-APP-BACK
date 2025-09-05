"""
app/config.py - Application configuration and Firebase initialization.

This module defines a Pydantic BaseSettings class to load configuration from environment,
and initializes Firebase Admin SDK (Firestore DB, Storage) using the provided credentials.
All other modules can import from config to access the `settings` and `db` (Firestore client).
"""
from pydantic import Field
import firebase_admin
from firebase_admin import credentials, firestore, storage
from pydantic_settings import BaseSettings   # ✅ BaseSettings buraya taşındı
from typing import Optional

class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""
    firebase_cred_file: str = Field(..., env='FIREBASE_CRED_FILE')
    firebase_project_id: str = Field(..., env='FIREBASE_PROJECT_ID')
    firebase_storage_bucket: str = Field(..., env='FIREBASE_STORAGE_BUCKET')
    iyzico_api_key: str = Field('', env='IYZICO_API_KEY')
    iyzico_secret_key: str = Field('', env='IYZICO_SECRET_KEY')
    iyzico_base_url: str = Field('https://sandbox-api.iyzipay.com', env='IYZICO_BASE_URL')
    tracking_api_key: str = Field('', env='TRACKING_API_KEY')
    debug: bool = Field(False, env='DEBUG')
    allowed_origins: str = Field('*', env='ALLOWED_ORIGINS')  # Comma-separated list or '*' for all
    firebase_web_api_key: str = Field(..., env="FIREBASE_WEB_API_KEY")
    delete_account_secret: Optional[str] = None
    smtp_host: str = "localhost"
    smtp_port: int = 465
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_from: Optional[str] = None
    smtp_use_starttls: bool = False  # 587 için true
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

# Load settings from environment (.env file, etc.)
settings = Settings()

# Initialize Firebase Admin SDK
# We use a service account credential file for full access to Firestore/Storage.
cred = credentials.Certificate(settings.firebase_cred_file)
firebase_app = firebase_admin.initialize_app(cred, {
    'projectId': settings.firebase_project_id,
    'storageBucket': settings.firebase_storage_bucket
})
# Create Firestore client and Storage bucket reference
db = firestore.client()  # Firestore database client
bucket = storage.bucket()  # Default storage bucket

# The `db` and `bucket` objects can now be used throughout the app for database and file operations.
