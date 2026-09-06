from enum import Enum
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from pydantic import BaseModel, Field
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


class Recommendation(BaseModel):
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
    reasoning_summary: str = Field(
        ..., description="Human-understandable summary of rule execution and recommendation rationale"
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when recommendation was calculated"
    )
