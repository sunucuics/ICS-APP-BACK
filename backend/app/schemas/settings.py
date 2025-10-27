# backend/app/schemas/settings.py
from pydantic import BaseModel, Field
from typing import Literal, Optional
from datetime import datetime

class BackupSettings(BaseModel):
    auto_backup: bool = Field(..., description="Otomatik yedekleme açık mı")
    backup_frequency: Literal["daily","weekly","monthly"] = Field(..., description="Sıklık")
    backup_retention_days: int = Field(..., ge=1, description="Kaç gün saklanacak")
    last_backup: Optional[datetime] = None
