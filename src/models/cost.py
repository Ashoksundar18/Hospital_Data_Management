from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field
from src.models.storage_object import StorageClass
from src.models.recommendation import RecommendedAction, ApprovalStatus


class CostEvaluationItem(BaseModel):
    object_id: str
    size_bytes: int
    current_class: StorageClass
    recommended_action: RecommendedAction
    target_storage_class: Optional[StorageClass] = None
    approval_status: ApprovalStatus = ApprovalStatus.PENDING
    execution_status: str = "pending"  # pending, pending_execution, executed, blocked_by_legal_hold, execution_failed, overridden, rolled_back
    legal_hold: bool = False


class SingleObjectCost(BaseModel):
    object_id: Optional[str] = None
    size_bytes: int
    size_gb: float
    current_storage_class: StorageClass
    target_storage_class: Optional[StorageClass] = None
    recommended_action: RecommendedAction
    current_monthly_cost: float
    projected_monthly_cost: float
    monthly_savings: float
    annual_savings: float
    is_blocked_by_legal_hold: bool = False
    rate_current_per_gb: float
    rate_target_per_gb: float


class CostCategorySummary(BaseModel):
    object_count: int = 0
    total_size_gb: float = 0.0
    baseline_monthly_cost: float = 0.0
    projected_monthly_cost: float = 0.0
    monthly_savings: float = 0.0
    annual_savings: float = 0.0


class CostReport(BaseModel):
    total_objects: int
    total_size_bytes: int
    total_size_gb: float
    total_baseline_monthly_cost: float
    total_baseline_annual_cost: float
    projected_monthly_cost_if_all_executed: float
    projected_annual_cost_if_all_executed: float
    
    # Savings metrics categorized by governance and execution status
    realized_monthly_savings: float = 0.0
    realized_annual_savings: float = 0.0
    approved_queued_monthly_savings: float = 0.0
    approved_queued_annual_savings: float = 0.0
    pending_review_monthly_savings: float = 0.0
    blocked_by_legal_hold_monthly_savings: float = 0.0
    total_realizable_monthly_savings: float = 0.0
    total_realizable_annual_savings: float = 0.0

    breakdown_by_status: Dict[str, CostCategorySummary] = Field(default_factory=dict)
    breakdown_by_action: Dict[str, CostCategorySummary] = Field(default_factory=dict)
    itemized_costs: Optional[List[SingleObjectCost]] = None
