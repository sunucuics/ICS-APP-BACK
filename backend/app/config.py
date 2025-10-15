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
from pydantic import Field
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
    iyzico_api_key: str = Field("", env="IYZICO_API_KEY")
    iyzico_secret_key: str = Field("", env="IYZICO_SECRET_KEY")
    iyzico_base_url: str = Field("https://sandbox-api.iyzipay.com", env="IYZICO_BASE_URL")

    # Aras
    ARAS_ENV: str = "prod"
    ARAS_USERNAME: str = ""
    ARAS_PASSWORD: str = ""
    ARAS_CUSTOMER_CODE: Optional[str] = None
    ARAS_SERVICE_URL: str = ""  # legacy ad
    ARAS_SOAPACTION_HINT: str = ""
    EXPOSE_SHIPPING_DEBUG: bool = False
    ARAS_TIMEOUT: int = 20
    ARAS_TRACKING_LINK_TEMPLATE: Optional[str] = None
    aras_base_url: Optional[str] = Field(None, env="ARAS_BASE_URL")
    ARAS_ENDPOINT: str = "https://appls-srv.araskargo.com.tr/arascargoservice/arascargoservice.asmx"
    ARAS_DEBUG: bool = Field(False, env="ARAS_DEBUG")
    ARAS_TEST_URL: str = "https://customerservicestest.araskargo.com.tr/arascargoservice/arascargoservice.asmx"
    ARAS_LIVE_URL: str = "https://customerws.araskargo.com.tr/arascargoservice.asmx"
    # App misc
    debug: bool = Field(False, env="DEBUG")
    allowed_origins: str = Field("*", env="ALLOWED_ORIGINS")
    firebase_web_api_key: str = Field(..., env="FIREBASE_WEB_API_KEY")

    # Mail
    delete_account_secret: Optional[str] = None
    smtp_host: str = "localhost"
    smtp_port: int = 465
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_from: Optional[str] = None
    smtp_use_starttls: bool = False  # 587 için true

    # Shipping options
    AUTO_LABEL: bool = False
    AUTO_PICKUP: bool = False
    PICKUP_TIME_WINDOW: str = "13:00-17:00"
    PICKUP_DAYS_OFFSET: int = 0
    LABEL_PUBLIC: bool = False
    LABEL_URL_EXPIRES_HOURS: int = 24
    ARAS_WEBHOOK_SECRET: str = ""
    ARAS_SOAP_VERSION: str = Field("auto", env="ARAS_SOAP_VERSION")

    class Config:
        env_file = ".env"
        case_sensitive = False

    @property
    def ARAS_BASE_URL(self) -> str:
        """
        Env'de ARAS_BASE_URL verilmişse onu ( ?op= / ?wsdl kırpıp ) kullan;
        verilmemişse ARAS_ENV'e göre doğru .asmx'i döndür.
        """
        url = (self.aras_base_url or "").strip()
        if url:
            if "?op=" in url:
                url = url.split("?op=", 1)[0]
            if url.endswith("?wsdl"):
                url = url[:-5]
            return url.rstrip("/")

        # ARAS_ENV'e göre default
        if self.ARAS_ENV.upper() in ("TEST", "SANDBOX"):
            return "https://customerservicestest.araskargo.com.tr/arascargoservice/arascargoservice.asmx"
        # customerws (prod)
        # Not: bazı kurulumlarda /arascargoservice.asmx dizin ismi farklı olabilir, integrations bunu varyant olarak dener.
        return "https://customerws.araskargo.com.tr/arascargoservice.asmx"

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
