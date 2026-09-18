import json
import os
import datetime
from typing import List, Optional, Dict, Any
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query, status, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from src.db import (
    engine as db_engine,
    SessionLocal,
    init_db,
    get_db,
    get_db_backend_info,
    StorageObjectDB,
    RetentionRuleDB,
    RecommendationDB,
    ConfirmationOverrideDB,
    AuditLogDB,
)
from src.models import (
    StorageObject,
    RetentionRule,
    Recommendation,
    RecommendedAction,
    DataClassification,
    ApprovalStatus,
    OverrideReasonTaxonomy,
    ConfirmRequest,
    OverrideRequest,
    RollbackRequest,
    PeriodicReviewRequest,
    AuditEntry,
    AuditVerifyResponse,
)
from src.engine import LifecycleRulesEngine
from src.services import write_audit_entry, verify_audit_chain


def preload_initial_data(db: Session):
    """Preloads retention rules and synthetic dataset into database if empty."""
    # Preload retention rules
    if db.query(RetentionRuleDB).count() == 0:
        retention_path = os.path.join("data", "retention_rules.json")
        if os.path.exists(retention_path):
            with open(retention_path, "r") as f:
                rules_raw = json.load(f)
                for r in rules_raw:
                    db_rule = RetentionRuleDB(
                        id=r["id"],
                        applies_to_classification=r.get("applies_to_classification"),
                        applies_to_bucket_pattern=r.get("applies_to_bucket_pattern"),
                        min_retention_days=r["min_retention_days"],
                        min_retrieval_tier=r.get("min_retrieval_tier"),
                        max_retrieval_latency_hours=r.get("max_retrieval_latency_hours"),
                        legal_hold_override_behavior=r.get("legal_hold_override_behavior", "PRESERVE_INDEFINITELY"),
                        jurisdiction=r.get("jurisdiction", "HIPAA_US"),
                        description=r["description"]
                    )
                    db.add(db_rule)
            db.commit()

    # Preload synthetic objects
    if db.query(StorageObjectDB).count() == 0:
        objects_path = os.path.join("data", "synthetic_objects.json")
        if os.path.exists(objects_path):
            with open(objects_path, "r") as f:
                objects_raw = json.load(f)
                for item in objects_raw:
                    db_obj = StorageObjectDB(
                        id=item["id"],
                        bucket_or_account=item["bucket_or_account"],
                        cloud_provider=item["cloud_provider"],
                        data_classification=item["data_classification"],
                        current_storage_class=item["current_storage_class"],
                        size_bytes=item["size_bytes"],
                        object_age_days=item["object_age_days"],
                        last_access_days_ago=item.get("last_access_days_ago"),
                        access_frequency_30d=item.get("access_frequency_30d"),
                        restore_event_history_json=json.dumps(item.get("restore_event_history")) if item.get("restore_event_history") is not None else None,
                        retention_rule_id=item.get("retention_rule_id"),
                        legal_hold=item.get("legal_hold", False)
                    )
                    db.add(db_obj)
            db.commit()

            # Write initial genesis audit log entry
            write_audit_entry(
                db,
                event_type="INITIALIZE_DATASET",
                actor="SYSTEM",
                details={"message": f"Preloaded synthetic dataset and retention rules into database ({db_engine.dialect.name})."}
            )


# Backward compatibility alias
preload_initial_data_to_sqlite = preload_initial_data


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    db = SessionLocal()
    try:
        preload_initial_data(db)
    finally:
        db.close()
    yield


app = FastAPI(
    title="Storage Lifecycle Recommender API (Phase 2 Governance)",
    description="Responsible-AI automated storage lifecycle recommender with PostgreSQL / SQLite persistence, human approval, and hash-chained audit logging.",
    version="2.0.0",
    lifespan=lifespan
)

# Mount UI static files if directory exists
if os.path.exists("ui"):
    app.mount("/static", StaticFiles(directory="ui"), name="static")


@app.get("/api/v1/db-info")
def get_db_info():
    """Returns active database backend dialect and engine metadata."""
    return get_db_backend_info()



@app.get("/", response_class=HTMLResponse)
def root_ui():
    """Serves prototype UI dashboard index file."""
    index_path = os.path.join("ui", "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Storage Lifecycle Recommender API Running</h1><p>Visit /docs for API documentation.</p>"


def db_obj_to_pydantic(db_obj: StorageObjectDB) -> StorageObject:
    restore_history = json.loads(db_obj.restore_event_history_json) if db_obj.restore_event_history_json else None
    return StorageObject(
        id=db_obj.id,
        bucket_or_account=db_obj.bucket_or_account,
        cloud_provider=db_obj.cloud_provider,
        data_classification=db_obj.data_classification,
        current_storage_class=db_obj.current_storage_class,
        size_bytes=db_obj.size_bytes,
        object_age_days=db_obj.object_age_days,
        last_access_days_ago=db_obj.last_access_days_ago,
        access_frequency_30d=db_obj.access_frequency_30d,
        restore_event_history=restore_history,
        retention_rule_id=db_obj.retention_rule_id,
        legal_hold=db_obj.legal_hold
    )


def db_rule_to_pydantic(db_rule: RetentionRuleDB) -> RetentionRule:
    return RetentionRule(
        id=db_rule.id,
        applies_to_classification=db_rule.applies_to_classification,
        applies_to_bucket_pattern=db_rule.applies_to_bucket_pattern,
        min_retention_days=db_rule.min_retention_days,
        min_retrieval_tier=db_rule.min_retrieval_tier,
        max_retrieval_latency_hours=db_rule.max_retrieval_latency_hours,
        legal_hold_override_behavior=db_rule.legal_hold_override_behavior,
        jurisdiction=db_rule.jurisdiction,
        description=db_rule.description
    )


# --- API ENDPOINTS ---

@app.post("/api/v1/objects", status_code=status.HTTP_201_CREATED)
def ingest_object(obj: StorageObject, db: Session = Depends(get_db)):
    """
    Ingests or updates a storage object's metadata in the SQLite database and appends an audit log entry.
    """
    db_obj = db.query(StorageObjectDB).filter(StorageObjectDB.id == obj.id).first()
    restore_json = json.dumps([e.model_dump() for e in obj.restore_event_history]) if obj.restore_event_history is not None else None

    if db_obj:
        db_obj.bucket_or_account = obj.bucket_or_account
        db_obj.cloud_provider = obj.cloud_provider.value
        db_obj.data_classification = obj.data_classification.value
        db_obj.current_storage_class = obj.current_storage_class.value
        db_obj.size_bytes = obj.size_bytes
        db_obj.object_age_days = obj.object_age_days
        db_obj.last_access_days_ago = obj.last_access_days_ago
        db_obj.access_frequency_30d = obj.access_frequency_30d
        db_obj.restore_event_history_json = restore_json
        db_obj.retention_rule_id = obj.retention_rule_id
        db_obj.legal_hold = obj.legal_hold
        action_verb = "updated"
    else:
        db_obj = StorageObjectDB(
            id=obj.id,
            bucket_or_account=obj.bucket_or_account,
            cloud_provider=obj.cloud_provider.value,
            data_classification=obj.data_classification.value,
            current_storage_class=obj.current_storage_class.value,
            size_bytes=obj.size_bytes,
            object_age_days=obj.object_age_days,
            last_access_days_ago=obj.last_access_days_ago,
            access_frequency_30d=obj.access_frequency_30d,
            restore_event_history_json=restore_json,
            retention_rule_id=obj.retention_rule_id,
            legal_hold=obj.legal_hold
        )
        db.add(db_obj)
        action_verb = "ingested"

    db.commit()

    write_audit_entry(
        db,
        event_type="INGEST_OBJECT",
        actor="API_CLIENT",
        object_id=obj.id,
        details={
            "action": action_verb,
            "cloud_provider": obj.cloud_provider.value,
            "classification": obj.data_classification.value,
            "size_bytes": obj.size_bytes,
            "legal_hold": obj.legal_hold
        }
    )

    return {
        "status": "success",
        "message": f"Storage object metadata {action_verb} successfully",
        "object_id": obj.id
    }


@app.get("/api/v1/objects", response_model=List[StorageObject])
def list_objects(
    classification: Optional[DataClassification] = None,
    legal_hold: Optional[bool] = None,
    db: Session = Depends(get_db)
):
    """
    Retrieves list of ingested storage objects from SQLite database.
    """
    query = db.query(StorageObjectDB)
    if classification:
        query = query.filter(StorageObjectDB.data_classification == classification.value)
    if legal_hold is not None:
        query = query.filter(StorageObjectDB.legal_hold == legal_hold)

    db_objects = query.all()
    return [db_obj_to_pydantic(o) for o in db_objects]


@app.get("/api/v1/recommendations", response_model=List[Recommendation])
def get_recommendations(
    classification: Optional[DataClassification] = None,
    action: Optional[RecommendedAction] = None,
    approval_status: Optional[ApprovalStatus] = None,
    missing_data_only: bool = False,
    db: Session = Depends(get_db)
):
    """
    Runs the rules engine on persisted storage objects, saves/syncs recommendations in DB, and returns structured outputs.
    Optimized with bulk DB queries and single-commit batch syncing for instant response times.
    """
    db_rules = db.query(RetentionRuleDB).all()
    pydantic_rules = [db_rule_to_pydantic(r) for r in db_rules]
    engine = LifecycleRulesEngine(retention_rules=pydantic_rules)

    db_objects = db.query(StorageObjectDB).all()
    pydantic_objects = [db_obj_to_pydantic(o) for o in db_objects]

    calculated_recs = engine.evaluate_batch(pydantic_objects)

    # 1. Bulk map of existing recommendation records
    existing_recs_map = {r.object_id: r for r in db.query(RecommendationDB).all()}

    # 2. Bulk map of latest human governance action per object
    gov_events = db.query(ConfirmationOverrideDB).order_by(ConfirmationOverrideDB.timestamp.asc()).all()
    gov_map = {}
    for g in gov_events:
        gov_map[g.object_id] = g

    output_recommendations = []
    has_changes = False

    for rec in calculated_recs:
        latest_gov = gov_map.get(rec.object_id)
        existing_rec = existing_recs_map.get(rec.object_id)

        # Determine canonical governance status from ConfirmationOverrideDB & RecommendationDB
        if latest_gov:
            if latest_gov.action_type == "CONFIRM":
                status_str = ApprovalStatus.CONFIRMED.value
            elif latest_gov.action_type == "OVERRIDE":
                status_str = ApprovalStatus.OVERRIDDEN.value
            elif latest_gov.action_type == "ROLLBACK":
                status_str = ApprovalStatus.ROLLED_BACK.value
            elif latest_gov.action_type == "PERIODIC_REVIEW":
                status_str = existing_rec.approval_status if existing_rec else ApprovalStatus.PENDING.value
            else:
                status_str = existing_rec.approval_status if existing_rec else ApprovalStatus.PENDING.value
        elif existing_rec:
            status_str = existing_rec.approval_status
        else:
            status_str = ApprovalStatus.PENDING.value

        last_reviewed = None
        if latest_gov and latest_gov.action_type == "PERIODIC_REVIEW":
            last_reviewed = latest_gov.timestamp
        elif existing_rec:
            last_reviewed = existing_rec.last_reviewed_at

        rec.approval_status = ApprovalStatus(status_str)
        rec.last_reviewed_at = last_reviewed

        if existing_rec:
            rec.id = existing_rec.id
            existing_rec.current_class = rec.current_class.value
            existing_rec.recommended_action = rec.recommended_action.value
            existing_rec.target_storage_class = rec.target_storage_class.value if rec.target_storage_class else None
            existing_rec.triggering_rules_json = json.dumps(rec.triggering_rules)
            existing_rec.evidence_snapshot_json = json.dumps(rec.evidence_snapshot)
            existing_rec.confidence_tier = rec.confidence_tier.value
            existing_rec.impact_tier = rec.impact_tier.value
            existing_rec.data_completeness_flag = rec.data_completeness_flag.value
            existing_rec.approval_status = status_str
            existing_rec.requires_periodic_review = rec.requires_periodic_review
            existing_rec.last_reviewed_at = last_reviewed
            existing_rec.reasoning_summary = rec.reasoning_summary
            has_changes = True
        else:
            db_rec = RecommendationDB(
                id=rec.id,
                object_id=rec.object_id,
                current_class=rec.current_class.value,
                recommended_action=rec.recommended_action.value,
                target_storage_class=rec.target_storage_class.value if rec.target_storage_class else None,
                triggering_rules_json=json.dumps(rec.triggering_rules),
                evidence_snapshot_json=json.dumps(rec.evidence_snapshot),
                confidence_tier=rec.confidence_tier.value,
                impact_tier=rec.impact_tier.value,
                data_completeness_flag=rec.data_completeness_flag.value,
                approval_status=status_str,
                requires_periodic_review=rec.requires_periodic_review,
                last_reviewed_at=last_reviewed,
                reasoning_summary=rec.reasoning_summary,
                timestamp=datetime.datetime.now(datetime.timezone.utc)
            )
            db.add(db_rec)
            existing_recs_map[rec.object_id] = db_rec
            has_changes = True

        # Apply endpoint query filters
        if classification and rec.evidence_snapshot.get("data_classification") != classification.value:
            continue
        if action and rec.recommended_action != action:
            continue
        if approval_status and rec.approval_status != approval_status:
            continue
        if missing_data_only and rec.confidence_tier != ConfidenceTier.LOW:
            continue

        output_recommendations.append(rec)

    if has_changes:
        db.commit()

    return output_recommendations


# --- HUMAN-IN-THE-LOOP GOVERNANCE ENDPOINTS ---

@app.post("/api/v1/recommendations/{rec_id_or_obj_id}/confirm", response_model=Recommendation)
def confirm_recommendation(
    rec_id_or_obj_id: str,
    body: ConfirmRequest,
    db: Session = Depends(get_db)
):
    """
    Phase 2: Confirms a recommendation. Updates approval_status to 'confirmed' and writes audit log.
    """
    rec_db = db.query(RecommendationDB).filter(
        (RecommendationDB.id == rec_id_or_obj_id) | (RecommendationDB.object_id == rec_id_or_obj_id)
    ).first()

    if not rec_db:
        raise HTTPException(status_code=404, detail=f"Recommendation for ID '{rec_id_or_obj_id}' not found")

    rec_db.approval_status = ApprovalStatus.CONFIRMED.value
    db.commit()

    # Record confirmation governance row
    conf_record = ConfirmationOverrideDB(
        id=f"conf-{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d%H%M%S')}-{rec_db.id[:6]}",
        recommendation_id=rec_db.id,
        object_id=rec_db.object_id,
        action_type="CONFIRM",
        reviewer_id=body.reviewer_id,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    db.add(conf_record)
    db.commit()

    write_audit_entry(
        db,
        event_type="CONFIRM_RECOMMENDATION",
        actor=body.reviewer_id,
        object_id=rec_db.object_id,
        recommendation_id=rec_db.id,
        details={
            "action": "CONFIRM",
            "reviewer_id": body.reviewer_id,
            "impact_tier": rec_db.impact_tier,
            "recommended_action": rec_db.recommended_action
        }
    )

    pydantic_obj = db_obj_to_pydantic(db.query(StorageObjectDB).filter(StorageObjectDB.id == rec_db.object_id).first())
    engine = LifecycleRulesEngine()
    rec = engine.evaluate_object(pydantic_obj)
    rec.id = rec_db.id
    rec.approval_status = ApprovalStatus.CONFIRMED
    rec.requires_periodic_review = rec_db.requires_periodic_review
    rec.last_reviewed_at = rec_db.last_reviewed_at
    return rec


@app.post("/api/v1/recommendations/{rec_id_or_obj_id}/override", response_model=Recommendation)
def override_recommendation(
    rec_id_or_obj_id: str,
    body: OverrideRequest,
    db: Session = Depends(get_db)
):
    """
    Phase 2: Overrides a recommendation with structured reason taxonomy. Updates approval_status to 'overridden' and writes audit log.
    """
    rec_db = db.query(RecommendationDB).filter(
        (RecommendationDB.id == rec_id_or_obj_id) | (RecommendationDB.object_id == rec_id_or_obj_id)
    ).first()

    if not rec_db:
        raise HTTPException(status_code=404, detail=f"Recommendation for ID '{rec_id_or_obj_id}' not found")

    rec_db.approval_status = ApprovalStatus.OVERRIDDEN.value
    db.commit()

    override_record = ConfirmationOverrideDB(
        id=f"ovr-{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d%H%M%S')}-{rec_db.id[:6]}",
        recommendation_id=rec_db.id,
        object_id=rec_db.object_id,
        action_type="OVERRIDE",
        reviewer_id=body.reviewer_id,
        override_reason_taxonomy=body.override_reason.value,
        other_reason_text=body.other_reason_text,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    db.add(override_record)
    db.commit()

    write_audit_entry(
        db,
        event_type="OVERRIDE_RECOMMENDATION",
        actor=body.reviewer_id,
        object_id=rec_db.object_id,
        recommendation_id=rec_db.id,
        details={
            "action": "OVERRIDE",
            "reviewer_id": body.reviewer_id,
            "override_reason_taxonomy": body.override_reason.value,
            "other_reason_text": body.other_reason_text,
            "impact_tier": rec_db.impact_tier
        }
    )

    pydantic_obj = db_obj_to_pydantic(db.query(StorageObjectDB).filter(StorageObjectDB.id == rec_db.object_id).first())
    engine = LifecycleRulesEngine()
    rec = engine.evaluate_object(pydantic_obj)
    rec.id = rec_db.id
    rec.approval_status = ApprovalStatus.OVERRIDDEN
    rec.requires_periodic_review = rec_db.requires_periodic_review
    rec.last_reviewed_at = rec_db.last_reviewed_at
    return rec


@app.post("/api/v1/recommendations/{rec_id_or_obj_id}/periodic-review", response_model=Recommendation)
def periodic_review_recommendation(
    rec_id_or_obj_id: str,
    body: PeriodicReviewRequest,
    db: Session = Depends(get_db)
):
    """
    Phase 2: Completes compliance periodic re-confirmation for NO_ACTION items (legal hold / retention lock).
    """
    rec_db = db.query(RecommendationDB).filter(
        (RecommendationDB.id == rec_id_or_obj_id) | (RecommendationDB.object_id == rec_id_or_obj_id)
    ).first()

    if not rec_db:
        raise HTTPException(status_code=404, detail=f"Recommendation for ID '{rec_id_or_obj_id}' not found")

    rec_db.last_reviewed_at = datetime.datetime.now(datetime.timezone.utc)
    db.commit()

    review_record = ConfirmationOverrideDB(
        id=f"rev-{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d%H%M%S')}-{rec_db.id[:6]}",
        recommendation_id=rec_db.id,
        object_id=rec_db.object_id,
        action_type="PERIODIC_REVIEW",
        reviewer_id=body.reviewer_id,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    db.add(review_record)
    db.commit()

    write_audit_entry(
        db,
        event_type="PERIODIC_REVIEW",
        actor=body.reviewer_id,
        object_id=rec_db.object_id,
        recommendation_id=rec_db.id,
        details={
            "action": "PERIODIC_REVIEW",
            "reviewer_id": body.reviewer_id,
            "reviewed_at": rec_db.last_reviewed_at.isoformat()
        }
    )

    pydantic_obj = db_obj_to_pydantic(db.query(StorageObjectDB).filter(StorageObjectDB.id == rec_db.object_id).first())
    engine = LifecycleRulesEngine()
    rec = engine.evaluate_object(pydantic_obj)
    rec.id = rec_db.id
    rec.approval_status = ApprovalStatus(rec_db.approval_status)
    rec.requires_periodic_review = rec_db.requires_periodic_review
    rec.last_reviewed_at = rec_db.last_reviewed_at
    return rec


@app.post("/api/v1/recommendations/{rec_id_or_obj_id}/rollback", response_model=Recommendation)
def rollback_recommendation(
    rec_id_or_obj_id: str,
    body: RollbackRequest,
    db: Session = Depends(get_db)
):
    """
    Phase 2: Governance-level rollback of a confirmed or overridden recommendation back to 'rolled_back' state.
    """
    rec_db = db.query(RecommendationDB).filter(
        (RecommendationDB.id == rec_id_or_obj_id) | (RecommendationDB.object_id == rec_id_or_obj_id)
    ).first()

    if not rec_db:
        raise HTTPException(status_code=404, detail=f"Recommendation for ID '{rec_id_or_obj_id}' not found")

    previous_status = rec_db.approval_status
    rec_db.approval_status = ApprovalStatus.ROLLED_BACK.value
    db.commit()

    rollback_record = ConfirmationOverrideDB(
        id=f"rlb-{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d%H%M%S')}-{rec_db.id[:6]}",
        recommendation_id=rec_db.id,
        object_id=rec_db.object_id,
        action_type="ROLLBACK",
        reviewer_id=body.reviewer_id,
        other_reason_text=body.reason,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    db.add(rollback_record)
    db.commit()

    write_audit_entry(
        db,
        event_type="ROLLBACK_RECOMMENDATION",
        actor=body.reviewer_id,
        object_id=rec_db.object_id,
        recommendation_id=rec_db.id,
        details={
            "action": "ROLLBACK",
            "reviewer_id": body.reviewer_id,
            "previous_approval_status": previous_status,
            "rollback_reason": body.reason
        }
    )

    pydantic_obj = db_obj_to_pydantic(db.query(StorageObjectDB).filter(StorageObjectDB.id == rec_db.object_id).first())
    engine = LifecycleRulesEngine()
    rec = engine.evaluate_object(pydantic_obj)
    rec.id = rec_db.id
    rec.approval_status = ApprovalStatus.ROLLED_BACK
    rec.requires_periodic_review = rec_db.requires_periodic_review
    rec.last_reviewed_at = rec_db.last_reviewed_at
    return rec


# --- AUDIT TRAIL ENDPOINTS ---

@app.get("/api/v1/audit-log", response_model=List[AuditEntry])
def get_audit_log(
    object_id: Optional[str] = None,
    event_type: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Retrieves the immutable, hash-chained audit log entries with optional filters.
    """
    query = db.query(AuditLogDB).order_by(AuditLogDB.id.asc())

    if object_id:
        query = query.filter(AuditLogDB.object_id == object_id)
    if event_type:
        query = query.filter(AuditLogDB.event_type == event_type)

    entries_db = query.all()
    results = []

    for e in entries_db:
        # Date filter parsing if provided
        if date_from:
            dt_from = datetime.datetime.fromisoformat(date_from.replace('Z', '+00:00'))
            if e.timestamp < dt_from:
                continue
        if date_to:
            dt_to = datetime.datetime.fromisoformat(date_to.replace('Z', '+00:00'))
            if e.timestamp > dt_to:
                continue

        results.append(AuditEntry(
            id=e.id,
            timestamp=e.timestamp,
            event_type=e.event_type,
            actor=e.actor,
            object_id=e.object_id,
            recommendation_id=e.recommendation_id,
            details=json.loads(e.details_json),
            previous_hash=e.previous_hash,
            entry_hash=e.entry_hash
        ))

    return results


@app.get("/api/v1/audit-log/verify", response_model=AuditVerifyResponse)
def verify_audit_log_chain(db: Session = Depends(get_db)):
    """
    Executes a cryptographic verification check across all audit log entries in sequence.
    Detects any database-level tampering or altered hash signatures.
    """
    total_entries = db.query(AuditLogDB).count()
    is_valid, tampered_id, message = verify_audit_chain(db)
    return AuditVerifyResponse(
        is_valid=is_valid,
        total_entries=total_entries,
        tampered_entry_id=tampered_id,
        message=message
    )
