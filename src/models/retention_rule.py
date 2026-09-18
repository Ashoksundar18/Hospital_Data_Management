from typing import Optional
from pydantic import BaseModel, Field, field_validator
from .storage_object import DataClassification, StorageClass


class RetentionRule(BaseModel):
    id: str = Field(..., description="Unique retention rule identifier")
    applies_to_classification: Optional[DataClassification] = Field(
        None, description="Data classification this retention rule applies to"
    )
    applies_to_bucket_pattern: Optional[str] = Field(
        None, description="Bucket glob pattern this rule applies to"
    )
    min_retention_days: int = Field(
        ..., ge=0, description="Minimum required retention period in days before deletion is permitted"
    )
    # Phase 2 Retrieval-SLA parameters
    min_retrieval_tier: Optional[StorageClass] = Field(
        None, description="Minimum allowed storage class tier while retention-locked (e.g. COLD or COOL). Transitions colder than this tier are blocked."
    )
    max_retrieval_latency_hours: Optional[int] = Field(
        None, ge=0, description="Maximum permitted data retrieval latency SLA in hours."
    )
    legal_hold_override_behavior: str = Field(
        "PRESERVE_INDEFINITELY",
        description="Behavior when legal hold is active (e.g. PRESERVE_INDEFINITELY)"
    )
    jurisdiction: str = Field(
        "HIPAA_US", description="Legal or regulatory jurisdiction (e.g. HIPAA_US, GDPR_EU)"
    )
    description: str = Field(..., description="Human-readable description of compliance requirement")

    @field_validator('id')
    def check_id_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Retention rule ID cannot be empty")
        return v.strip()
