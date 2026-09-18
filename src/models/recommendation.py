from enum import Enum
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
import uuid
from pydantic import BaseModel, Field, model_validator
from .storage_object import StorageClass


class RecommendedAction(str, Enum):
    NO_ACTION = "NO_ACTION"
    TRANSITION = "TRANSITION"
    DELETE = "DELETE"


class ConfidenceTier(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ImpactTier(str, Enum):
    LOW_IMPACT = "LOW_IMPACT"
    MEDIUM_IMPACT = "MEDIUM_IMPACT"
    HIGH_IMPACT = "HIGH_IMPACT"


class DataCompletenessFlag(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL_MISSING_ACCESS = "PARTIAL_MISSING_ACCESS"
    PARTIAL_MISSING_RESTORE = "PARTIAL_MISSING_RESTORE"
    INCOMPLETE = "INCOMPLETE"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    OVERRIDDEN = "overridden"
    ROLLED_BACK = "rolled_back"


class OverrideReasonTaxonomy(str, Enum):
    PENDING_CLINICAL_TRIAL = "PENDING_CLINICAL_TRIAL"
    LITIGATION_HOLD_EXTENDED = "LITIGATION_HOLD_EXTENDED"
    CUSTOM_SLA = "CUSTOM_SLA"
    DATA_QUALITY_CONCERN = "DATA_QUALITY_CONCERN"
    OTHER = "OTHER"


class Recommendation(BaseModel):
    id: Optional[str] = Field(
        default_factory=lambda: f"rec-{uuid.uuid4().hex[:8]}",
        description="Unique recommendation ID"
    )
    object_id: str = Field(..., description="Target object ID")
    current_class: StorageClass = Field(..., description="Current storage class tier")
    recommended_action: RecommendedAction = Field(..., description="Recommended lifecycle action")
    target_storage_class: Optional[StorageClass] = Field(
        None, description="Recommended target storage class if action is TRANSITION"
    )
    triggering_rules: List[str] = Field(
        ..., description="List of specific rule codes triggered during evaluation"
    )
    evidence_snapshot: Dict[str, Any] = Field(
        ..., description="Input data values and snapshot facts that led to the decision"
    )
    confidence_tier: ConfidenceTier = Field(
        ..., description="Confidence tier (HIGH, MEDIUM, LOW) based on telemetry availability"
    )
    impact_tier: ImpactTier = Field(
        ..., description="Impact tier assessing deletion/transition risk"
    )
    data_completeness_flag: DataCompletenessFlag = Field(
        DataCompletenessFlag.COMPLETE, description="Flag indicating missing telemetry status"
    )
    approval_status: ApprovalStatus = Field(
        ApprovalStatus.PENDING, description="Human approval governance status (pending, confirmed, overridden, rolled_back)"
    )
    requires_periodic_review: bool = Field(
        False, description="Flag for compliance-sensitive NO_ACTION outcomes requiring periodic re-confirmation"
    )
    last_reviewed_at: Optional[datetime] = Field(
        None, description="Timestamp when periodic review was last completed"
    )
    reasoning_summary: str = Field(
        ..., description="Human-understandable summary of rule execution and recommendation rationale"
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when recommendation was calculated"
    )


# --- Phase 2 Human Workflow & Audit Request/Response Models ---

class ConfirmRequest(BaseModel):
    reviewer_id: str = Field(..., description="Identifier of the human reviewer approving the recommendation")


class OverrideRequest(BaseModel):
    reviewer_id: str = Field(..., description="Identifier of the human reviewer overriding the recommendation")
    override_reason: OverrideReasonTaxonomy = Field(..., description="Structured taxonomy reason for override")
    other_reason_text: Optional[str] = Field(None, description="Detailed explanation required if override_reason is OTHER")

    @model_validator(mode='after')
    def validate_other_text(self):
        if self.override_reason == OverrideReasonTaxonomy.OTHER:
            if not self.other_reason_text or not self.other_reason_text.strip():
                raise ValueError("Free-text detail 'other_reason_text' is strictly required when override_reason is OTHER")
        return self


class RollbackRequest(BaseModel):
    reviewer_id: str = Field(..., description="Identifier of the reviewer initiating governance rollback")
    reason: str = Field(..., description="Reason for rolling back the confirmed/overridden recommendation state")

    @model_validator(mode='after')
    def validate_reason(self):
        if not self.reason or not self.reason.strip():
            raise ValueError("Rollback reason cannot be empty")
        return self


class PeriodicReviewRequest(BaseModel):
    reviewer_id: str = Field(..., description="Identifier of the reviewer confirming compliance periodic review")


class AuditEntry(BaseModel):
    id: int = Field(..., description="Auto-incremented audit log entry ID")
    timestamp: datetime = Field(..., description="Event timestamp")
    event_type: str = Field(..., description="Type of event (INGEST_OBJECT, CONFIRM_RECOMMENDATION, etc.)")
    actor: str = Field(..., description="Reviewer or system component triggering the event")
    object_id: Optional[str] = Field(None, description="Associated storage object ID")
    recommendation_id: Optional[str] = Field(None, description="Associated recommendation ID")
    details: Dict[str, Any] = Field(..., description="Structured event payload")
    previous_hash: str = Field(..., description="SHA-256 hash of previous audit log entry")
    entry_hash: str = Field(..., description="SHA-256 cryptographic hash of this entry")


class AuditVerifyResponse(BaseModel):
    is_valid: bool = Field(..., description="True if cryptographic hash chain is untampered and 100% valid")
    total_entries: int = Field(..., description="Total number of audit log entries evaluated")
    tampered_entry_id: Optional[int] = Field(None, description="ID of first tampered entry if chain invalid")
    message: str = Field(..., description="Status summary message")
