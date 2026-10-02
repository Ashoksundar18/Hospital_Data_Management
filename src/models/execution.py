from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
from pydantic import BaseModel, Field


class ExecuteRequest(BaseModel):
    confirm_irreversible: bool = Field(
        False,
        description="Explicit confirmation flag required when executing permanent DELETE actions or DEEP_ARCHIVE transitions"
    )
    irreversible_justification: Optional[str] = Field(
        None,
        description="Non-empty justification required when executing irreversible actions"
    )


class ExecutionResult(BaseModel):
    recommendation_id: str
    object_id: str
    action: str
    status: str  # EXECUTED, EXECUTION_FAILED, BLOCKED_BY_LEGAL_HOLD
    details: Dict[str, Any]
    executed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class BatchRunRequest(BaseModel):
    bucket_or_account: Optional[str] = Field(None, description="Optional target bucket to restrict batch execution")
    dry_run: bool = Field(False, description="If true, previews executions without mutating cloud storage")
    max_items: int = Field(100, ge=1, le=1000, description="Maximum number of confirmed recommendations to process")
    confirm_irreversible: bool = Field(False, description="Authorize permanent DELETE and DEEP_ARCHIVE actions")
    irreversible_justification: Optional[str] = Field(None, description="Justification required if confirm_irreversible=True")


class BatchRunSummary(BaseModel):
    batch_id: str
    dry_run: bool
    total_candidates: int
    executed_count: int = 0
    failed_count: int = 0
    blocked_by_legal_hold_count: int = 0
    skipped_irreversible_count: int = 0
    items: List[Dict[str, Any]] = Field(default_factory=list)
    started_at: datetime
    completed_at: datetime
    duration_seconds: float

