import os
import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.api.main import app
from src.db import Base, get_db, StorageObjectDB, RetentionRuleDB, RecommendationDB, AuditLogDB, init_db
from src.models import (
    StorageObject,
    CloudProvider,
    DataClassification,
    StorageClass,
    RetentionRule,
    RecommendedAction,
    ApprovalStatus,
    OverrideReasonTaxonomy,
    ConfidenceTier,
)
from src.engine import LifecycleRulesEngine
from src.services import write_audit_entry, verify_audit_chain

client = TestClient(app)


def test_db_persistence_across_restart(tmp_path):
    """
    1. Database Persistence Test: Confirm object metadata and recommendation history
    survive a server restart (process termination and DB reconnection).
    """
    test_db_file = os.path.join(tmp_path, "test_persistence.db")
    test_engine = create_engine(f"sqlite:///{test_db_file}")
    TestingSession = sessionmaker(bind=test_engine)

    Base.metadata.create_all(bind=test_engine)
    db1 = TestingSession()

    # Ingest object & write recommendation to DB
    obj = StorageObjectDB(
        id="obj-persist-999",
        bucket_or_account="hospital-persist-bucket",
        cloud_provider="AWS",
        data_classification="MEDICAL_IMAGE",
        current_storage_class="HOT",
        size_bytes=1000000,
        object_age_days=100,
        legal_hold=False
    )
    rec = RecommendationDB(
        id="rec-persist-999",
        object_id="obj-persist-999",
        current_class="HOT",
        recommended_action="TRANSITION",
        target_storage_class="COOL",
        triggering_rules_json=json.dumps(["RULE_AGE_ACCESS_TRANSITION"]),
        evidence_snapshot_json=json.dumps({"test": "data"}),
        confidence_tier="HIGH",
        impact_tier="HIGH_IMPACT",
        data_completeness_flag="COMPLETE",
        approval_status="confirmed",
        requires_periodic_review=False,
        reasoning_summary="Persisted test recommendation"
    )
    db1.add(obj)
    db1.add(rec)
    db1.commit()
    db1.close()

    # SIMULATE APP RESTART: Dispose old engine connection, create brand new Session
    test_engine.dispose()
    
    new_engine = create_engine(f"sqlite:///{test_db_file}")
    RestartedSession = sessionmaker(bind=new_engine)
    db2 = RestartedSession()

    # Verify object and recommendation history survived intact across restart
    restarted_obj = db2.query(StorageObjectDB).filter(StorageObjectDB.id == "obj-persist-999").first()
    restarted_rec = db2.query(RecommendationDB).filter(RecommendationDB.id == "rec-persist-999").first()

    assert restarted_obj is not None
    assert restarted_obj.bucket_or_account == "hospital-persist-bucket"
    assert restarted_rec is not None
    assert restarted_rec.approval_status == "confirmed"
    assert restarted_rec.recommended_action == "TRANSITION"

    db2.close()


def test_retrieval_sla_rule_enforcement():
    """
    2. Retrieval-SLA Rule Test:
    (a) Retention rule with min_retrieval_tier blocks/downgrades colder transitions.
    (b) Retention rule with min_retrieval_tier=None falls back cleanly to Option-A behavior.
    """
    rule_with_sla = RetentionRule(
        id="rule-sla-cool",
        applies_to_classification=DataClassification.APP_LOG,
        min_retention_days=365,
        min_retrieval_tier=StorageClass.COOL,  # CANNOT GO COLDER THAN COOL
        max_retrieval_latency_hours=1,
        description="SLA requiring online tier"
    )

    engine = LifecycleRulesEngine(retention_rules=[rule_with_sla])

    # Object would normally qualify for ARCHIVE tier transition (age 120d log in COOL tier)
    obj = StorageObject(
        id="obj-sla-test",
        bucket_or_account="hospital-logs",
        cloud_provider=CloudProvider.AWS,
        data_classification=DataClassification.APP_LOG,
        current_storage_class=StorageClass.COOL,
        size_bytes=1000,
        object_age_days=120,
        access_frequency_30d=0,
        retention_rule_id="rule-sla-cool",
        legal_hold=False
    )

    rec = engine.evaluate_object(obj)

    # RULE_RETRIEVAL_SLA MUST block transition to ARCHIVE because min_retrieval_tier is COOL
    assert rec.recommended_action == RecommendedAction.NO_ACTION
    assert "RULE_RETRIEVAL_SLA" in rec.triggering_rules
    assert "retrieval sla" in rec.reasoning_summary.lower()

    # Case (b): Rule without min_retrieval_tier falls back to Option-A standard transition
    rule_option_a = RetentionRule(
        id="rule-option-a",
        applies_to_classification=DataClassification.APP_LOG,
        min_retention_days=365,
        min_retrieval_tier=None,  # No retrieval constraint
        description="Standard Option-A retention rule"
    )
    engine_a = LifecycleRulesEngine(retention_rules=[rule_option_a])
    rec_a = engine_a.evaluate_object(obj)

    assert rec_a.recommended_action == RecommendedAction.TRANSITION
    assert rec_a.target_storage_class == StorageClass.ARCHIVE


def test_confirm_and_override_workflows():
    """
    3. Human Approval & Override Test:
    (a) Confirm endpoint updates status to 'confirmed'.
    (b) Override with valid taxonomy updates status to 'overridden'.
    (c) Override with OTHER taxonomy without free-text is rejected.
    """
    with TestClient(app) as client:
        # Ingest test object
        obj_payload = {
            "id": "obj-human-workflow-01",
            "bucket_or_account": "hospital-test",
            "cloud_provider": "AWS",
            "data_classification": "BACKUP",
            "current_storage_class": "HOT",
            "size_bytes": 1000000,
            "object_age_days": 120,
            "access_frequency_30d": 0,
            "retention_rule_id": "rule-backup-dr-retention",
            "legal_hold": False
        }
        client.post("/api/v1/objects", json=obj_payload)
        client.get("/api/v1/recommendations")  # Sync recs to DB

        # (a) Confirm
        res_conf = client.post(
            "/api/v1/recommendations/obj-human-workflow-01/confirm",
            json={"reviewer_id": "usr-admin-01"}
        )
        assert res_conf.status_code == 200
        assert res_conf.json()["approval_status"] == "confirmed"

        # (b) Override with valid taxonomy
        res_ovr = client.post(
            "/api/v1/recommendations/obj-human-workflow-01/override",
            json={
                "reviewer_id": "usr-admin-02",
                "override_reason": "PENDING_CLINICAL_TRIAL"
            }
        )
        assert res_ovr.status_code == 200
        assert res_ovr.json()["approval_status"] == "overridden"

        # (c) Override with OTHER taxonomy but missing other_reason_text -> Rejected with 422
        res_invalid = client.post(
            "/api/v1/recommendations/obj-human-workflow-01/override",
            json={
                "reviewer_id": "usr-admin-03",
                "override_reason": "OTHER",
                "other_reason_text": ""  # Invalid empty string
            }
        )
        assert res_invalid.status_code == 422


def test_compliance_periodic_review():
    """
    4. Compliance Periodic Review Test:
    Compliance-sensitive NO_ACTION items (Legal Hold) set requires_periodic_review=True.
    """
    with TestClient(app) as client:
        obj_payload = {
            "id": "obj-periodic-review-01",
            "bucket_or_account": "hospital-legal-hold",
            "cloud_provider": "GCP",
            "data_classification": "MEDICAL_IMAGE",
            "current_storage_class": "HOT",
            "size_bytes": 1000000,
            "object_age_days": 500,
            "legal_hold": True
        }
        client.post("/api/v1/objects", json=obj_payload)
        recs = client.get("/api/v1/recommendations").json()
        target_rec = next(r for r in recs if r["object_id"] == "obj-periodic-review-01")

        assert target_rec["requires_periodic_review"] is True

        # Submit periodic review
        res_rev = client.post(
            f"/api/v1/recommendations/{target_rec['id']}/periodic-review",
            json={"reviewer_id": "usr-hipaa-officer"}
        )
        assert res_rev.status_code == 200
        assert res_rev.json()["last_reviewed_at"] is not None


def test_hash_chained_audit_log_and_tamper_detection(tmp_path):
    """
    5. Hash-Chained Audit Log & Tamper Detection Test:
    (a) Appends audit log entries with valid SHA-256 hash signatures.
    (b) Deliberately tampers with a DB entry and verifies verify_audit_chain detects it.
    """
    test_db_file = os.path.join(tmp_path, "test_audit.db")
    test_engine = create_engine(f"sqlite:///{test_db_file}")
    TestingSession = sessionmaker(bind=test_engine)

    Base.metadata.create_all(bind=test_engine)
    db = TestingSession()

    # Append two valid audit entries
    e1 = write_audit_entry(db, event_type="INGEST_OBJECT", actor="USR_1", object_id="obj-01", details={"key": "val1"})
    e2 = write_audit_entry(db, event_type="CONFIRM_RECOMMENDATION", actor="USR_2", object_id="obj-01", details={"key": "val2"})

    # Verify chain is untampered
    is_valid, tampered_id, msg = verify_audit_chain(db)
    assert is_valid is True
    assert tampered_id is None

    # DELIBERATE TAMPERING: Alter payload content directly in DB
    e1_db = db.query(AuditLogDB).filter(AuditLogDB.id == e1.id).first()
    e1_db.details_json = json.dumps({"key": "TAMPERED_VAL"})
    db.commit()

    # Re-verify chain -> Must detect tampering!
    is_valid_tampered, tampered_id_detected, msg_tampered = verify_audit_chain(db)
    assert is_valid_tampered is False
    assert tampered_id_detected == e1.id
    assert "tamper" in msg_tampered.lower()

    db.close()


def test_governance_rollback_workflow():
    """
    6. Rollback Workflow Test:
    Rollback updates approval status to 'rolled_back' and logs a ROLLBACK audit event.
    """
    with TestClient(app) as client:
        obj_payload = {
            "id": "obj-rollback-01",
            "bucket_or_account": "hospital-test",
            "cloud_provider": "AWS",
            "data_classification": "APP_LOG",
            "current_storage_class": "HOT",
            "size_bytes": 100000,
            "object_age_days": 100,
            "access_frequency_30d": 0,
            "legal_hold": False
        }
        client.post("/api/v1/objects", json=obj_payload)
        client.get("/api/v1/recommendations")

        # Confirm first
        client.post("/api/v1/recommendations/obj-rollback-01/confirm", json={"reviewer_id": "usr-admin"})

        # Execute Rollback
        res_rlb = client.post(
            "/api/v1/recommendations/obj-rollback-01/rollback",
            json={"reviewer_id": "usr-audit-lead", "reason": "Accidental confirmation"}
        )
        assert res_rlb.status_code == 200
        assert res_rlb.json()["approval_status"] == "rolled_back"

        # Verify audit log recorded ROLLBACK_RECOMMENDATION event
        audit_res = client.get("/api/v1/audit-log?object_id=obj-rollback-01").json()
        assert any(a["event_type"] == "ROLLBACK_RECOMMENDATION" for a in audit_res)


def test_preexisting_retrieval_sla_violation():
    """
    7. Pre-existing Retrieval SLA Violation Test:
    When an object's current storage class ALREADY violates min_retrieval_tier
    (e.g., current_storage_class=ARCHIVE, min_retrieval_tier=COOL):
    - Recommended action MUST be NO_ACTION.
    - Triggering rules MUST contain RULE_RETRIEVAL_SLA.
    - requires_periodic_review MUST be True (flagged for compliance review).
    - Reasoning summary MUST explicitly state "Pre-existing Retrieval SLA Violation".
    """
    rule_cool = RetentionRule(
        id="rule-sla-cool-strict",
        applies_to_classification=DataClassification.MEDICAL_IMAGE,
        min_retention_days=365,
        min_retrieval_tier=StorageClass.COOL,  # Minimum retrieval tier is COOL
        description="SLA requiring at least COOL retrieval tier"
    )

    engine = LifecycleRulesEngine(retention_rules=[rule_cool])

    # Object is already in ARCHIVE tier (colder than COOL)
    obj_in_violation = StorageObject(
        id="obj-preexisting-sla-01",
        bucket_or_account="hospital-archive-bucket",
        cloud_provider=CloudProvider.AWS,
        data_classification=DataClassification.MEDICAL_IMAGE,
        current_storage_class=StorageClass.ARCHIVE,  # VIOLATION: ARCHIVE is index 3 > COOL (index 1)
        size_bytes=5000000,
        object_age_days=100,
        access_frequency_30d=0,
        retention_rule_id="rule-sla-cool-strict",
        legal_hold=False
    )

    rec = engine.evaluate_object(obj_in_violation)

    assert rec.recommended_action == RecommendedAction.NO_ACTION
    assert rec.target_storage_class is None
    assert "RULE_RETRIEVAL_SLA" in rec.triggering_rules
    assert rec.requires_periodic_review is True
    assert rec.confidence_tier == ConfidenceTier.HIGH
    assert "pre-existing retrieval sla violation" in rec.reasoning_summary.lower()
    assert rec.evidence_snapshot.get("preexisting_retrieval_sla_violation") is True


def test_render_missing_database_url_raises_runtime_error(monkeypatch):
    """
    Verifies that if RENDER=true is set and DATABASE_URL is missing,
    initializing the database module raises a clear RuntimeError.
    """
    monkeypatch.setenv("RENDER", "true")
    for var in ["DATABASE_URL", "Database_Url", "INTERNAL_DATABASE_URL", "EXTERNAL_DATABASE_URL", "POSTGRES_URL", "POSTGRESQL_URL"]:
        monkeypatch.delenv(var, raising=False)

    import importlib
    import src.db.database
    with pytest.raises(RuntimeError, match="DATABASE_URL environment variable is required when running on Render"):
        importlib.reload(src.db.database)


def test_no_duplicate_initialize_dataset_audit_entry_on_reboot():
    """
    Verifies that startup preloading does not duplicate the INITIALIZE_DATASET audit entry
    when objects already exist in the database.
    """
    with TestClient(app) as client:
        # First call triggers startup lifespan and preloading
        res1 = client.get("/api/v1/audit-log?event_type=INITIALIZE_DATASET")
        assert res1.status_code == 200
        initial_entries = res1.json()

        # Simulate secondary startup / preload call on existing DB session
        from src.api.main import preload_initial_data
        from src.db import SessionLocal
        db = SessionLocal()
        try:
            preload_initial_data(db)
        finally:
            db.close()

        res2 = client.get("/api/v1/audit-log?event_type=INITIALIZE_DATASET")
        assert res2.status_code == 200
        reboot_entries = res2.json()

        assert len(reboot_entries) == len(initial_entries)


