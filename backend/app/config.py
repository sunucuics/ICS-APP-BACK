# backend/app/config.py
"""
app/config.py - Application configuration and Firebase initialization.

This module defines a Pydantic BaseSettings class to load configuration from environment,
and initializes Firebase Admin SDK (Firestore DB, Storage) using the provided credentials.
All other modules can import from config to access the `settings` and `db` (Firestore client).
"""
from typing import Optional

import firebase_admin
from firebase_admin import credentials, firestore, storage
from pydantic import Field , AliasChoices
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    # Firebase
    firebase_cred_file: str = Field("firebase_service_account.json", env="FIREBASE_CRED_FILE")
    firebase_project_id: str = Field(..., env="FIREBASE_PROJECT_ID")
    firebase_storage_bucket: str = Field(..., env="FIREBASE_STORAGE_BUCKET")

    # Firebase credentials via env (Cloud Run)
    firebase_private_key_id: Optional[str] = Field(None, env="FIREBASE_PRIVATE_KEY_ID")
    firebase_private_key: Optional[str] = Field(None, env="FIREBASE_PRIVATE_KEY")
    firebase_client_email: Optional[str] = Field(None, env="FIREBASE_CLIENT_EMAIL")
    firebase_client_id: Optional[str] = Field(None, env="FIREBASE_CLIENT_ID")
    firebase_auth_uri: Optional[str] = Field(None, env="FIREBASE_AUTH_URI")
    firebase_token_uri: Optional[str] = Field(None, env="FIREBASE_TOKEN_URI")
    firebase_auth_provider_x509_cert_url: Optional[str] = Field(None, env="FIREBASE_AUTH_PROVIDER_X509_CERT_URL")
    firebase_client_x509_cert_url: Optional[str] = Field(None, env="FIREBASE_CLIENT_X509_CERT_URL")

    # Iyzico
    paytr_merchant_id: str   = Field(validation_alias=AliasChoices("PAYTR_MERCHANT_ID", "paytr_merchant_id"))
    paytr_merchant_key: str  = Field(validation_alias=AliasChoices("PAYTR_MERCHANT_KEY", "paytr_merchant_key"))
    paytr_merchant_salt: str = Field(validation_alias=AliasChoices("PAYTR_MERCHANT_SALT", "paytr_merchant_salt"))
    paytr_ok_url: str        = Field(default="https://example.com/payment/success", validation_alias=AliasChoices("PAYTR_OK_URL","paytr_ok_url"))
    paytr_fail_url: str      = Field(default="https://example.com/payment/fail",    validation_alias=AliasChoices("PAYTR_FAIL_URL","paytr_fail_url"))
    paytr_test_mode: str     = Field(default="1", validation_alias=AliasChoices("PAYTR_TEST_MODE","paytr_test_mode"))


    # App misc
    # App misc
    debug: bool = Field(False, env="DEBUG")
    allowed_origins: str = Field("*", env="ALLOWED_ORIGINS")
    firebase_web_api_key: str = Field(..., env="FIREBASE_WEB_API_KEY")

    # Mail  ✅ ENV bağlama eklendi + opsiyonel alanlar
    delete_account_secret: Optional[str] = None
    smtp_host: str = Field("localhost", env="SMTP_HOST")
    smtp_port: int = Field(465, env="SMTP_PORT")
    smtp_user: Optional[str] = Field(None, env="SMTP_USER")
    smtp_password: Optional[str] = Field(None, env="SMTP_PASSWORD")
    smtp_from: Optional[str] = Field(None, env="SMTP_FROM")
    smtp_use_starttls: bool = Field(False, env="SMTP_USE_STARTTLS")
    email_debug: bool = Field(False, env="EMAIL_DEBUG")
    brand_name: Optional[str] = Field("ICS", env="BRAND_NAME")
    frontend_base_url: Optional[str] = Field("", env="FRONTEND_BASE_URL")

    debug_mail_token: Optional[str] = Field(None, env="DEBUG_MAIL_TOKEN")



    class Config:
        env_file = ".env"
        case_sensitive = False

    def model_post_init(self, __context):
        """Validate Firebase Web API Key format"""
        if not self.firebase_web_api_key or not self.firebase_web_api_key.startswith("AIza"):
            raise ValueError("FIREBASE_WEB_API_KEY must be a valid Firebase Web API Key starting with 'AIza'")


# Load settings
settings = Settings()

# Initialize Firebase Admin SDK (local file veya env dict)
try:
    if all(
        [
            settings.firebase_private_key_id,
            settings.firebase_private_key,
            settings.firebase_client_email,
            settings.firebase_client_id,
            settings.firebase_auth_uri,
            settings.firebase_token_uri,
            settings.firebase_auth_provider_x509_cert_url,
            settings.firebase_client_x509_cert_url,
        ]
    ):
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
            "client_x509_cert_url": settings.firebase_client_x509_cert_url,
        }
        cred = credentials.Certificate(cred_dict)
    else:
        cred = credentials.Certificate(settings.firebase_cred_file)

    firebase_app = firebase_admin.initialize_app(
        cred,
        {
            "projectId": settings.firebase_project_id,
            "storageBucket": settings.firebase_storage_bucket,
        },
    )
except ValueError as e:
    if "already exists" in str(e):
        firebase_app = firebase_admin.get_app()
    else:
        raise

# Firestore & Storage
db = firestore.client()
bucket = storage.bucket()
