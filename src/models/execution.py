from typing import Optional, Dict, Any
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
