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
    firebase_cred_file: str = Field('firebase_service_account.json', env='FIREBASE_CRED_FILE')
    firebase_project_id: str = Field(..., env='FIREBASE_PROJECT_ID')
    firebase_storage_bucket: str = Field(..., env='FIREBASE_STORAGE_BUCKET')
    
    # Firebase credentials from environment variables (for Cloud Run)
    firebase_private_key_id: Optional[str] = Field(None, env='FIREBASE_PRIVATE_KEY_ID')
    firebase_private_key: Optional[str] = Field(None, env='FIREBASE_PRIVATE_KEY')
    firebase_client_email: Optional[str] = Field(None, env='FIREBASE_CLIENT_EMAIL')
    firebase_client_id: Optional[str] = Field(None, env='FIREBASE_CLIENT_ID')
    firebase_auth_uri: Optional[str] = Field(None, env='FIREBASE_AUTH_URI')
    firebase_token_uri: Optional[str] = Field(None, env='FIREBASE_TOKEN_URI')
    firebase_auth_provider_x509_cert_url: Optional[str] = Field(None, env='FIREBASE_AUTH_PROVIDER_X509_CERT_URL')
    firebase_client_x509_cert_url: Optional[str] = Field(None, env='FIREBASE_CLIENT_X509_CERT_URL')
    iyzico_api_key: str = Field('', env='IYZICO_API_KEY')
    iyzico_secret_key: str = Field('', env='IYZICO_SECRET_KEY')
    iyzico_base_url: str = Field('https://sandbox-api.iyzipay.com', env='IYZICO_BASE_URL')

    ARAS_ENV: str = "TEST"                 # TEST | PROD
    ARAS_USERNAME: str = ""
    ARAS_PASSWORD: str = ""
    ARAS_CUSTOMER_CODE: str | None = None
    ARAS_TIMEOUT: int = 15
    ARAS_TRACKING_LINK_TEMPLATE: str | None = None

    @property
    def ARAS_BASE_URL(self) -> str:
        """ARAS_ENV'e göre doğru SOAP endpointini döndürür."""
        return (
            "https://customerservicestest.araskargo.com.tr/arascargoservice/arascargoservice.asmx"
            if self.ARAS_ENV.upper() == "TEST"
            else "https://customerws.araskargo.com.tr/arascargoservice.asmx"
        )

    debug: bool = Field(False, env='DEBUG')
    allowed_origins: str = Field('*', env='ALLOWED_ORIGINS')  # Comma-separated list or '*' for all
    firebase_web_api_key: str = Field(..., env="FIREBASE_WEB_API_KEY")
    
    def model_post_init(self, __context):
        """Validate Firebase Web API Key format"""
        if not self.firebase_web_api_key or not self.firebase_web_api_key.startswith('AIza'):
            raise ValueError("FIREBASE_WEB_API_KEY must be a valid Firebase Web API Key starting with 'AIza'")
    delete_account_secret: Optional[str] = None
    smtp_host: str = "localhost"
    smtp_port: int = 465
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_from: Optional[str] = None
    smtp_use_starttls: bool = False  # 587 için true

    ARAS_ENV: str = "TEST"
    AUTO_LABEL: bool = False
    AUTO_PICKUP: bool = False
    PICKUP_TIME_WINDOW: str = "13:00-17:00"
    PICKUP_DAYS_OFFSET: int = 0
    LABEL_PUBLIC: bool = False
    LABEL_URL_EXPIRES_HOURS: int = 24
    ARAS_WEBHOOK_SECRET: str = ""

    class Config:
        env_file = ".env"
        case_sensitive = False

# Load settings from environment (.env file, etc.)
settings = Settings()

# Initialize Firebase Admin SDK
# We use a service account credential file for full access to Firestore/Storage.
try:
    # Check if we have environment variables for Firebase credentials (Cloud Run)
    if all([
        settings.firebase_private_key_id,
        settings.firebase_private_key,
        settings.firebase_client_email,
        settings.firebase_client_id,
        settings.firebase_auth_uri,
        settings.firebase_token_uri,
        settings.firebase_auth_provider_x509_cert_url,
        settings.firebase_client_x509_cert_url
    ]):
        # Use environment variables for Firebase credentials (Cloud Run)
        cred_dict = {
            "type": "service_account",
            "project_id": settings.firebase_project_id,
            "private_key_id": settings.firebase_private_key_id,
            "private_key": settings.firebase_private_key,
            "client_email": settings.firebase_client_email,
            "client_id": settings.firebase_client_id,
            "auth_uri": settings.firebase_auth_uri,
            "token_uri": settings.firebase_token_uri,
            "auth_provider_x509_cert_url": settings.firebase_auth_provider_x509_cert_url,
            "client_x509_cert_url": settings.firebase_client_x509_cert_url
        }
        cred = credentials.Certificate(cred_dict)
    else:
        # Use service account file (local development)
        cred = credentials.Certificate(settings.firebase_cred_file)
    
    firebase_app = firebase_admin.initialize_app(cred, {
        'projectId': settings.firebase_project_id,
        'storageBucket': settings.firebase_storage_bucket
    })
except ValueError as e:
    if "already exists" in str(e):
        # Firebase app already initialized, get the default app
        firebase_app = firebase_admin.get_app()
    else:
        raise
# Create Firestore client and Storage bucket reference
db = firestore.client()  # Firestore database client
bucket = storage.bucket()  # Default storage bucket

# The `db` and `bucket` objects can now be used throughout the app for database and file operations.
