import logging
from typing import Optional, Dict, Any
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from src.models.storage_object import StorageClass
from src.models.recommendation import RecommendedAction, ApprovalStatus, Recommendation
from src.db.db_models import StorageObjectDB, RecommendationDB
from src.services.cloud_executor import BaseStorageExecutor, S3StorageExecutor
from src.services.audit_service import write_audit_entry
from src.engine import LifecycleRulesEngine

logger = logging.getLogger(__name__)


def execute_recommendation_action(
    db: Session,
    rec_id_or_obj_id: str,
    actor: str,
    confirm_irreversible: bool = False,
    irreversible_justification: Optional[str] = None,
    executor: Optional[BaseStorageExecutor] = None,
    get_rec_lock_func = None
) -> Recommendation:
    """
    Executes a confirmed recommendation against the cloud storage provider.
    Re-validates legal_hold and retention rules at execution time.
    Enforces explicit confirm_irreversible flags for DELETE and DEEP_ARCHIVE actions.
    Appends immutable EXECUTE_TRANSITION / EXECUTE_DELETE / EXECUTION_FAILED audit entries.
    """
    if get_rec_lock_func:
        rec_db = get_rec_lock_func(db, rec_id_or_obj_id)
    else:
        query = db.query(RecommendationDB).filter(
            (RecommendationDB.id == rec_id_or_obj_id) | (RecommendationDB.object_id == rec_id_or_obj_id)
        )
        if db.bind and db.bind.dialect.name == "postgresql":
            query = query.with_for_update()
        rec_db = query.first()

    if not rec_db:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Recommendation for ID '{rec_id_or_obj_id}' not found"
        )

    obj_db = db.query(StorageObjectDB).filter(StorageObjectDB.id == rec_db.object_id).first()
    if not obj_db:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Storage object '{rec_db.object_id}' not found"
        )

    # 1. Idempotency Check: If already executed, return current record without re-invoking cloud API or writing duplicate audit log
    if rec_db.approval_status == ApprovalStatus.EXECUTED.value:
        from src.api.main import db_obj_to_pydantic
        pydantic_obj = db_obj_to_pydantic(obj_db)
        engine = LifecycleRulesEngine()
        rec = engine.evaluate_object(pydantic_obj)
        rec.id = rec_db.id
        rec.approval_status = ApprovalStatus.EXECUTED
        rec.requires_periodic_review = rec_db.requires_periodic_review
        rec.last_reviewed_at = rec_db.last_reviewed_at
        return rec

    # State Check: Execution strictly requires recommendation to be 'confirmed'
    if rec_db.approval_status != ApprovalStatus.CONFIRMED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot execute recommendation '{rec_id_or_obj_id}': status is '{rec_db.approval_status}', must be in 'confirmed' status."
        )

    # 2. Execution-Time Re-validation: Recheck legal hold immediately before cloud execution
    if obj_db.legal_hold:
        if rec_db.recommended_action in [RecommendedAction.TRANSITION.value, RecommendedAction.DELETE.value] or rec_db.target_storage_class:
            logger.warning(
                f"Execution blocked: Object '{rec_db.object_id}' is under legal hold. Halting execution for actor '{actor}'."
            )
            rec_db.approval_status = "blocked_by_legal_hold"
            write_audit_entry(
                db,
                event_type="EXECUTION_BLOCKED_LEGAL_HOLD",
                actor=actor,
                object_id=rec_db.object_id,
                recommendation_id=rec_db.id,
                details={
                    "reason": "Execution blocked: Object is currently under legal hold.",
                    "recommended_action": rec_db.recommended_action,
                    "target_storage_class": rec_db.target_storage_class
                },
                auto_commit=True
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Execution blocked: Object is currently under legal hold."
            )

    # 3. Irreversibility Safeguard: Require explicit confirmation for DELETE and DEEP_ARCHIVE
    is_irreversible = (
        rec_db.recommended_action == RecommendedAction.DELETE.value or
        rec_db.target_storage_class == StorageClass.DEEP_ARCHIVE.value
    )
    if is_irreversible:
        if not confirm_irreversible:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This action is irreversible. The 'confirm_irreversible' parameter must be true."
            )
        justification = (irreversible_justification or "").strip()
        if not justification:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A non-empty 'irreversible_justification' is strictly required when executing an irreversible action."
            )

    # 4. Invoke Cloud Provider API
    cloud_exec = executor or S3StorageExecutor()

    try:
        if rec_db.recommended_action == RecommendedAction.TRANSITION.value:
            target_tier = StorageClass(rec_db.target_storage_class)
            cloud_res = cloud_exec.transition_object(
                bucket=obj_db.bucket_or_account,
                key=obj_db.id,
                target_class=target_tier
            )
            event_type = "EXECUTE_TRANSITION"
            # Update local state to reflect successful cloud transition
            obj_db.current_storage_class = rec_db.target_storage_class

        elif rec_db.recommended_action == RecommendedAction.DELETE.value:
            cloud_res = cloud_exec.delete_object(
                bucket=obj_db.bucket_or_account,
                key=obj_db.id
            )
            event_type = "EXECUTE_DELETE"
        else:
            cloud_res = {"status": "SUCCESS", "action": "NO_ACTION"}
            event_type = "EXECUTE_NO_ACTION"

        rec_db.approval_status = "executed"

        write_audit_entry(
            db,
            event_type=event_type,
            actor=actor,
            object_id=rec_db.object_id,
            recommendation_id=rec_db.id,
            details={
                "cloud_result": cloud_res,
                "recommended_action": rec_db.recommended_action,
                "target_storage_class": rec_db.target_storage_class,
                "irreversible_justification": irreversible_justification if is_irreversible else None
            },
            auto_commit=False
        )
        db.commit()
        db.refresh(rec_db)

        # Build output recommendation model
        from src.api.main import db_obj_to_pydantic
        pydantic_obj = db_obj_to_pydantic(obj_db)
        engine = LifecycleRulesEngine()
        rec = engine.evaluate_object(pydantic_obj)
        rec.id = rec_db.id
        rec.approval_status = ApprovalStatus(rec_db.approval_status)
        rec.requires_periodic_review = rec_db.requires_periodic_review
        rec.last_reviewed_at = rec_db.last_reviewed_at
        return rec

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Cloud execution failed for recommendation '{rec_db.id}': {e}")
        rec_db.approval_status = ApprovalStatus.EXECUTION_FAILED.value
        db.commit()
        write_audit_entry(
            db,
            event_type="EXECUTION_FAILED",
            actor=actor,
            object_id=rec_db.object_id,
            recommendation_id=rec_db.id,
            details={
                "error": str(e),
                "recommended_action": rec_db.recommended_action,
                "target_storage_class": rec_db.target_storage_class
            },
            auto_commit=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Cloud execution failed: {e}"
        ) from e
