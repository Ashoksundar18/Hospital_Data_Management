import uuid
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session

from src.models.recommendation import ApprovalStatus, RecommendedAction
from src.models.storage_object import StorageClass
from src.models.execution import BatchRunSummary
from src.db.db_models import RecommendationDB, StorageObjectDB
from src.services.execution_service import execute_recommendation_action
from src.services.cloud_executor import BaseStorageExecutor
from src.services.audit_service import write_audit_entry

logger = logging.getLogger(__name__)


def run_batch_execution(
    db: Session,
    actor: str,
    bucket_or_account: Optional[str] = None,
    dry_run: bool = False,
    max_items: int = 100,
    confirm_irreversible: bool = False,
    irreversible_justification: Optional[str] = None,
    executor: Optional[BaseStorageExecutor] = None
) -> BatchRunSummary:
    """
    Executes confirmed recommendations in batch.
    Supports dry_run preview mode and optional bucket filtering.
    Re-validates legal holds and safeguards irreversible operations.
    Appends a BATCH_EXECUTION_RUN audit record with full metrics.
    """
    started_at = datetime.now(timezone.utc)
    batch_id = f"batch-{uuid.uuid4().hex[:8]}"

    # Fetch candidate recommendations in 'confirmed' status
    query = (
        db.query(RecommendationDB)
        .filter(RecommendationDB.approval_status == ApprovalStatus.CONFIRMED.value)
    )
    if bucket_or_account:
        query = query.join(StorageObjectDB, RecommendationDB.object_id == StorageObjectDB.id).filter(
            StorageObjectDB.bucket_or_account == bucket_or_account
        )

    candidates = query.order_by(RecommendationDB.timestamp.desc()).limit(max_items).all()

    total_candidates = len(candidates)
    executed_count = 0
    failed_count = 0
    blocked_by_legal_hold_count = 0
    skipped_irreversible_count = 0
    items_log: List[Dict[str, Any]] = []

    logger.info(f"Initiating batch execution '{batch_id}' (dry_run={dry_run}) with {total_candidates} candidates")

    for rec in candidates:
        obj = db.query(StorageObjectDB).filter(StorageObjectDB.id == rec.object_id).first()
        is_irreversible = (
            rec.recommended_action == RecommendedAction.DELETE.value or
            rec.target_storage_class == StorageClass.DEEP_ARCHIVE.value
        )

        if dry_run:
            if obj and obj.legal_hold:
                item_status = "WOULD_BLOCK_LEGAL_HOLD"
                blocked_by_legal_hold_count += 1
            elif is_irreversible and not confirm_irreversible:
                item_status = "WOULD_SKIP_IRREVERSIBLE"
                skipped_irreversible_count += 1
            else:
                item_status = "WOULD_EXECUTE"
                # In dry_run, executed_count remains 0 (preview only)

            items_log.append({
                "recommendation_id": rec.id,
                "object_id": rec.object_id,
                "action": rec.recommended_action,
                "target_storage_class": rec.target_storage_class,
                "status": item_status
            })
        else:
            # Active execution
            try:
                execute_recommendation_action(
                    db=db,
                    rec_id_or_obj_id=rec.id,
                    actor=actor,
                    confirm_irreversible=confirm_irreversible,
                    irreversible_justification=irreversible_justification,
                    executor=executor
                )
                executed_count += 1
                items_log.append({
                    "recommendation_id": rec.id,
                    "object_id": rec.object_id,
                    "action": rec.recommended_action,
                    "target_storage_class": rec.target_storage_class,
                    "status": "EXECUTED"
                })
            except Exception as e:
                err_msg = str(e).lower()
                if "legal hold" in err_msg:
                    blocked_by_legal_hold_count += 1
                    status_val = "BLOCKED_BY_LEGAL_HOLD"
                elif "irreversible" in err_msg:
                    skipped_irreversible_count += 1
                    status_val = "SKIPPED_IRREVERSIBLE"
                else:
                    failed_count += 1
                    status_val = "FAILED"

                items_log.append({
                    "recommendation_id": rec.id,
                    "object_id": rec.object_id,
                    "action": rec.recommended_action,
                    "target_storage_class": rec.target_storage_class,
                    "status": status_val,
                    "error": str(e)
                })

    completed_at = datetime.now(timezone.utc)
    duration_seconds = round((completed_at - started_at).total_seconds(), 3)

    # Write BATCH_EXECUTION_RUN audit record
    write_audit_entry(
        db,
        event_type="BATCH_EXECUTION_RUN",
        actor=actor,
        details={
            "batch_id": batch_id,
            "dry_run": dry_run,
            "total_candidates": total_candidates,
            "executed_count": executed_count,
            "failed_count": failed_count,
            "blocked_by_legal_hold_count": blocked_by_legal_hold_count,
            "skipped_irreversible_count": skipped_irreversible_count,
            "duration_seconds": duration_seconds
        },
        auto_commit=True
    )

    return BatchRunSummary(
        batch_id=batch_id,
        dry_run=dry_run,
        total_candidates=total_candidates,
        executed_count=executed_count,
        failed_count=failed_count,
        blocked_by_legal_hold_count=blocked_by_legal_hold_count,
        skipped_irreversible_count=skipped_irreversible_count,
        items=items_log,
        started_at=started_at,
        completed_at=completed_at,
        duration_seconds=duration_seconds
    )
