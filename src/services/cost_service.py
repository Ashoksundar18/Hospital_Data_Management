from typing import List, Dict, Optional, Any
from sqlalchemy.orm import Session

from src.models.storage_object import StorageClass
from src.models.recommendation import RecommendedAction, ApprovalStatus
from src.models.cost import (
    CostEvaluationItem,
    SingleObjectCost,
    CostCategorySummary,
    CostReport
)
from src.db.db_models import StorageObjectDB, RecommendationDB

# AWS S3 standard pricing per GB-month (US-East-1 baseline)
S3_STORAGE_PRICING: Dict[StorageClass, float] = {
    StorageClass.HOT: 0.023,           # S3 Standard
    StorageClass.COOL: 0.0125,         # S3 Standard-IA
    StorageClass.COLD: 0.004,          # S3 Glacier Instant Retrieval
    StorageClass.ARCHIVE: 0.0036,      # S3 Glacier Flexible Retrieval
    StorageClass.DEEP_ARCHIVE: 0.00099 # S3 Glacier Deep Archive
}

BYTES_PER_GB: int = 1024 ** 3


def calculate_object_cost(
    size_bytes: int,
    current_class: StorageClass,
    recommended_action: RecommendedAction,
    target_storage_class: Optional[StorageClass] = None,
    legal_hold: bool = False,
    object_id: Optional[str] = None
) -> SingleObjectCost:
    """
    Computes before and after storage costs for an individual storage object.
    """
    size_gb = size_bytes / BYTES_PER_GB
    rate_current = S3_STORAGE_PRICING.get(current_class, 0.023)

    if recommended_action == RecommendedAction.DELETE:
        rate_target = 0.0
    elif recommended_action == RecommendedAction.TRANSITION and target_storage_class:
        rate_target = S3_STORAGE_PRICING.get(target_storage_class, rate_current)
    else:
        rate_target = rate_current

    current_monthly_cost = round(size_gb * rate_current, 6)
    projected_monthly_cost = round(size_gb * rate_target, 6)
    monthly_savings = round(current_monthly_cost - projected_monthly_cost, 6)
    annual_savings = round(monthly_savings * 12, 6)

    return SingleObjectCost(
        object_id=object_id,
        size_bytes=size_bytes,
        size_gb=round(size_gb, 4),
        current_storage_class=current_class,
        target_storage_class=target_storage_class if recommended_action == RecommendedAction.TRANSITION else None,
        recommended_action=recommended_action,
        current_monthly_cost=current_monthly_cost,
        projected_monthly_cost=projected_monthly_cost,
        monthly_savings=monthly_savings,
        annual_savings=annual_savings,
        is_blocked_by_legal_hold=legal_hold,
        rate_current_per_gb=rate_current,
        rate_target_per_gb=rate_target
    )


def generate_cost_report(
    items: List[CostEvaluationItem],
    include_itemized: bool = False
) -> CostReport:
    """
    Produces a comprehensive before/after storage cost report aggregated across items.
    """
    total_objects = len(items)
    total_size_bytes = sum(i.size_bytes for i in items)
    total_size_gb = round(total_size_bytes / BYTES_PER_GB, 4)

    total_baseline_monthly = 0.0
    total_projected_monthly = 0.0

    realized_monthly_savings = 0.0
    approved_queued_monthly_savings = 0.0
    pending_review_monthly_savings = 0.0
    blocked_by_legal_hold_monthly_savings = 0.0

    breakdown_by_status: Dict[str, CostCategorySummary] = {}
    breakdown_by_action: Dict[str, CostCategorySummary] = {
        RecommendedAction.NO_ACTION.value: CostCategorySummary(),
        RecommendedAction.TRANSITION.value: CostCategorySummary(),
        RecommendedAction.DELETE.value: CostCategorySummary()
    }
    itemized: List[SingleObjectCost] = []

    for item in items:
        calc = calculate_object_cost(
            size_bytes=item.size_bytes,
            current_class=item.current_class,
            recommended_action=item.recommended_action,
            target_storage_class=item.target_storage_class,
            legal_hold=item.legal_hold,
            object_id=item.object_id
        )
        if include_itemized:
            itemized.append(calc)

        total_baseline_monthly += calc.current_monthly_cost
        
        # If not blocked by legal hold, savings are possible
        if item.legal_hold:
            blocked_by_legal_hold_monthly_savings += calc.monthly_savings
            total_projected_monthly += calc.current_monthly_cost
        else:
            total_projected_monthly += calc.projected_monthly_cost

            if item.execution_status == "executed":
                realized_monthly_savings += calc.monthly_savings
            elif item.approval_status == ApprovalStatus.CONFIRMED or item.execution_status == "pending_execution":
                approved_queued_monthly_savings += calc.monthly_savings
            else:
                pending_review_monthly_savings += calc.monthly_savings

        # Track category status breakdown
        status_key = "blocked_by_legal_hold" if item.legal_hold else item.execution_status
        if status_key not in breakdown_by_status:
            breakdown_by_status[status_key] = CostCategorySummary()
        cat_s = breakdown_by_status[status_key]
        cat_s.object_count += 1
        cat_s.total_size_gb += calc.size_gb
        cat_s.baseline_monthly_cost += calc.current_monthly_cost
        cat_s.projected_monthly_cost += calc.projected_monthly_cost
        cat_s.monthly_savings += calc.monthly_savings
        cat_s.annual_savings += calc.annual_savings

        # Track action breakdown
        action_key = item.recommended_action.value
        if action_key in breakdown_by_action:
            cat_a = breakdown_by_action[action_key]
            cat_a.object_count += 1
            cat_a.total_size_gb += calc.size_gb
            cat_a.baseline_monthly_cost += calc.current_monthly_cost
            cat_a.projected_monthly_cost += calc.projected_monthly_cost
            cat_a.monthly_savings += calc.monthly_savings
            cat_a.annual_savings += calc.annual_savings

    total_realizable_monthly = realized_monthly_savings + approved_queued_monthly_savings
    total_baseline_annual = total_baseline_monthly * 12
    total_projected_annual = total_projected_monthly * 12

    return CostReport(
        total_objects=total_objects,
        total_size_bytes=total_size_bytes,
        total_size_gb=round(total_size_gb, 4),
        total_baseline_monthly_cost=round(total_baseline_monthly, 4),
        total_baseline_annual_cost=round(total_baseline_annual, 4),
        projected_monthly_cost_if_all_executed=round(total_projected_monthly, 4),
        projected_annual_cost_if_all_executed=round(total_projected_annual, 4),
        realized_monthly_savings=round(realized_monthly_savings, 4),
        realized_annual_savings=round(realized_monthly_savings * 12, 4),
        approved_queued_monthly_savings=round(approved_queued_monthly_savings, 4),
        approved_queued_annual_savings=round(approved_queued_monthly_savings * 12, 4),
        pending_review_monthly_savings=round(pending_review_monthly_savings, 4),
        blocked_by_legal_hold_monthly_savings=round(blocked_by_legal_hold_monthly_savings, 4),
        total_realizable_monthly_savings=round(total_realizable_monthly, 4),
        total_realizable_annual_savings=round(total_realizable_monthly * 12, 4),
        breakdown_by_status=breakdown_by_status,
        breakdown_by_action=breakdown_by_action,
        itemized_costs=itemized if include_itemized else None
    )


def generate_cost_report_from_db(db: Session, include_itemized: bool = False) -> CostReport:
    """
    Loads all objects and their latest recommendations from the database to generate a CostReport.
    """
    objects = db.query(StorageObjectDB).all()
    recommendations = {r.object_id: r for r in db.query(RecommendationDB).all()}

    items: List[CostEvaluationItem] = []
    for obj in objects:
        rec = recommendations.get(obj.id)
        if rec:
            rec_action = RecommendedAction(rec.recommended_action)
            target_class = StorageClass(rec.target_storage_class) if rec.target_storage_class else None
            appr_status = ApprovalStatus(rec.approval_status) if rec.approval_status in [s.value for s in ApprovalStatus] else ApprovalStatus.PENDING
            
            if obj.legal_hold:
                exec_status = "blocked_by_legal_hold"
            elif rec.approval_status == "executed":
                exec_status = "executed"
            elif rec.approval_status == "confirmed":
                exec_status = "pending_execution"
            else:
                exec_status = rec.approval_status
        else:
            rec_action = RecommendedAction.NO_ACTION
            target_class = None
            appr_status = ApprovalStatus.PENDING
            exec_status = "pending"

        items.append(CostEvaluationItem(
            object_id=obj.id,
            size_bytes=obj.size_bytes,
            current_class=StorageClass(obj.current_storage_class),
            recommended_action=rec_action,
            target_storage_class=target_class,
            approval_status=appr_status,
            execution_status=exec_status,
            legal_hold=obj.legal_hold
        ))

    return generate_cost_report(items, include_itemized=include_itemized)
