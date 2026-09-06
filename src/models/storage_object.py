from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, field_validator


class CloudProvider(str, Enum):
    AWS = "AWS"
    AZURE = "AZURE"
    GCP = "GCP"


class DataClassification(str, Enum):
    MEDICAL_IMAGE = "MEDICAL_IMAGE"
    APP_LOG = "APP_LOG"
    BACKUP = "BACKUP"


class StorageClass(str, Enum):
    HOT = "HOT"
    COOL = "COOL"
    COLD = "COLD"
    ARCHIVE = "ARCHIVE"
    DEEP_ARCHIVE = "DEEP_ARCHIVE"


class RestoreEvent(BaseModel):
    days_ago: int = Field(..., ge=0, description="Days since restore event occurred")
    reason: Optional[str] = Field(None, description="Reason for restore request")


class StorageObject(BaseModel):
    id: str = Field(..., description="Unique identifier for the storage object")
    bucket_or_account: str = Field(..., description="Bucket or account identifier where object resides")
    cloud_provider: CloudProvider = Field(..., description="Cloud provider hosting the object")
    data_classification: DataClassification = Field(..., description="Data type classification")
    current_storage_class: StorageClass = Field(..., description="Current storage class tier")
    size_bytes: int = Field(..., ge=0, description="Object size in bytes")
    object_age_days: int = Field(..., ge=0, description="Age of object in days since creation")
    
    # Telemetry fields (may be None if feed is missing/delayed)
    last_access_days_ago: Optional[int] = Field(None, ge=0, description="Days since last access")
    access_frequency_30d: Optional[int] = Field(None, ge=0, description="Number of access requests in past 30 days")
    restore_event_history: Optional[List[RestoreEvent]] = Field(default=None, description="History of restore events")
    
    # Compliance & Lifecycle tracking
    retention_rule_id: Optional[str] = Field(None, description="Reference to applicable RetentionRule ID")
    legal_hold: bool = Field(False, description="Flag indicating if object is under legal hold")

    @field_validator('id', 'bucket_or_account')

    def check_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Field cannot be empty")
        return v.strip()
