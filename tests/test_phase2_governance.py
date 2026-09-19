import os
import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.api.main import app
from src.db import Base, get_db, StorageObjectDB, RetentionRuleDB, RecommendationDB, AuditLogDB, init_db, ensure_indexes
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
        assert len(initial_entries) == 1

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


def test_resolve_database_url_priority_and_normalization():
    """
    Tests resolve_database_url deterministic priority, case-insensitivity,
    whitespace/empty skipping, quote stripping, and normalization.
    """
    from src.db.database import resolve_database_url

    # (a) DATABASE_URL beats INTERNAL_DATABASE_URL and EXTERNAL_DATABASE_URL
    env_a = {
        "DATABASE_URL": "postgres://db1",
        "INTERNAL_DATABASE_URL": "postgres://db2",
        "EXTERNAL_DATABASE_URL": "postgres://db3",
    }
    assert resolve_database_url(env_a) == "postgresql://db1"

    # (b) with only INTERNAL_DATABASE_URL and EXTERNAL_DATABASE_URL set, INTERNAL_DATABASE_URL wins
    env_b = {
        "INTERNAL_DATABASE_URL": "postgres://db2",
        "EXTERNAL_DATABASE_URL": "postgres://db3",
    }
    assert resolve_database_url(env_b) == "postgresql://db2"

    # (c) mixed-case key such as Database_Url is found
    env_c = {
        "Database_Url": "postgresql://db_mixed",
    }
    assert resolve_database_url(env_c) == "postgresql://db_mixed"

    # (d) empty or whitespace-only values are skipped
    env_d = {
        "DATABASE_URL": "",
        "INTERNAL_DATABASE_URL": "   ",
        "EXTERNAL_DATABASE_URL": "postgres://real_ext",
    }
    assert resolve_database_url(env_d) == "postgresql://real_ext"

    # (e) surrounding quotes are stripped
    env_e = {
        "DATABASE_URL": '"postgres://quoted_db"',
    }
    assert resolve_database_url(env_e) == "postgresql://quoted_db"

    # (f) returns None when nothing is set
    env_f = {
        "SOME_OTHER_VAR": "value",
    }
    assert resolve_database_url(env_f) is None


def test_audit_log_concurrency_and_unique_hashes():
    """
    Verifies that concurrent confirmations on different objects produce a valid,
    untampered audit chain where every previous_hash and entry_hash is unique.
    """
    import concurrent.futures

    with TestClient(app) as client:
        # Ingest 10 test objects
        obj_ids = [f"obj-concurrent-{i:02d}" for i in range(10)]
        for oid in obj_ids:
            client.post("/api/v1/objects", json={
                "id": oid,
                "bucket_or_account": "hospital-concurrent-bucket",
                "cloud_provider": "AWS",
                "data_classification": "BACKUP",
                "current_storage_class": "HOT",
                "size_bytes": 10000,
                "object_age_days": 100,
                "legal_hold": False
            })
        client.get("/api/v1/recommendations")

        def confirm_worker(oid):
            with TestClient(app) as worker_client:
                return worker_client.post(
                    f"/api/v1/recommendations/{oid}/confirm",
                    json={"reviewer_id": f"usr-worker-{oid}"}
                )

        # Execute 10 parallel confirmations using ThreadPoolExecutor
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(confirm_worker, oid) for oid in obj_ids]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        for res in results:
            assert res.status_code == 200

        # Verify audit chain integrity across all entries
        from src.db import SessionLocal
        from src.services.audit_service import GENESIS_HASH
        db = SessionLocal()
        try:
            is_valid, tampered_id, message = verify_audit_chain(db)
            assert is_valid is True, f"Audit chain verification failed: {message}"
            assert tampered_id is None

            entries = db.query(AuditLogDB).all()
            hashes = [e.entry_hash for e in entries]
            prev_hashes = [e.previous_hash for e in entries]

            # Every entry_hash must be unique
            assert len(hashes) == len(set(hashes))
            # Every previous_hash (except GENESIS_HASH) must be unique
            non_genesis_prev = [p for p in prev_hashes if p != GENESIS_HASH]
            assert len(non_genesis_prev) == len(set(non_genesis_prev))
        finally:
            db.close()


def test_confirm_recommendation_idempotency():
    """
    Verifies that calling confirm multiple times on the same recommendation
    returns HTTP 200 with current state and does NOT create duplicate audit log entries.
    """
    with TestClient(app) as client:
        # Ingest object
        client.post("/api/v1/objects", json={
            "id": "obj-idempotent-01",
            "bucket_or_account": "b-idempotent",
            "cloud_provider": "AWS",
            "data_classification": "BACKUP",
            "current_storage_class": "HOT",
            "size_bytes": 1000,
            "object_age_days": 120,
            "legal_hold": False
        })
        client.get("/api/v1/recommendations")

        # First confirm
        res1 = client.post("/api/v1/recommendations/obj-idempotent-01/confirm", json={"reviewer_id": "usr-1"})
        assert res1.status_code == 200
        assert res1.json()["approval_status"] == "confirmed"

        audit_count_before = len(client.get("/api/v1/audit-log?object_id=obj-idempotent-01").json())

        # Second confirm (duplicate call)
        res2 = client.post("/api/v1/recommendations/obj-idempotent-01/confirm", json={"reviewer_id": "usr-1"})
        assert res2.status_code == 200
        assert res2.json()["approval_status"] == "confirmed"

        audit_count_after = len(client.get("/api/v1/audit-log?object_id=obj-idempotent-01").json())

        # No new audit log entries should have been created
        assert audit_count_after == audit_count_before


def test_rollback_non_confirmed_recommendation_returns_409_conflict():
    """
    Verifies that attempting to rollback a recommendation that is NOT in 'confirmed' or 'overridden'
    status is rejected with HTTP 409 Conflict.
    """
    with TestClient(app) as client:
        client.post("/api/v1/objects", json={
            "id": "obj-invalid-rollback-01",
            "bucket_or_account": "b-rollback",
            "cloud_provider": "AWS",
            "data_classification": "APP_LOG",
            "current_storage_class": "HOT",
            "size_bytes": 1000,
            "object_age_days": 100,
            "legal_hold": False
        })
        client.get("/api/v1/recommendations")

        # Recommendation status is 'pending', attempting rollback should return 409
        res_rlb = client.post(
            "/api/v1/recommendations/obj-invalid-rollback-01/rollback",
            json={"reviewer_id": "usr-audit", "reason": "Invalid rollback attempt"}
        )
        assert res_rlb.status_code == 409
        assert "must be confirmed or overridden" in res_rlb.json()["detail"]


def test_legal_hold_and_delete_justification_enforcement():
    """
    Verifies server-side legal hold protection and justification requirements:
    - Confirming/overriding transition or deletion on legal_hold=True object returns 400.
    - Confirming NO_ACTION on legal_hold=True object succeeds.
    - Confirming/overriding DELETE action without non-empty justification returns 400.
    - Confirming DELETE action with valid justification succeeds.
    """
    with TestClient(app) as client:
        # 1. Ingest object with legal_hold=False so it gets a TRANSITION recommendation
        client.post("/api/v1/objects", json={
            "id": "obj-lh-transition-01",
            "bucket_or_account": "b-legal-hold",
            "cloud_provider": "AWS",
            "data_classification": "BACKUP",
            "current_storage_class": "HOT",
            "size_bytes": 1000,
            "object_age_days": 120,
            "legal_hold": False
        })
        client.get("/api/v1/recommendations")

        # Now put object under legal hold (e.g. legal hold is enabled after rec was generated)
        from src.db import SessionLocal, StorageObjectDB
        db = SessionLocal()
        try:
            obj_db = db.query(StorageObjectDB).filter(StorageObjectDB.id == "obj-lh-transition-01").first()
            obj_db.legal_hold = True
            db.commit()
        finally:
            db.close()

        # Confirm transition on legal hold object -> 400 Bad Request
        res_conf_lh = client.post("/api/v1/recommendations/obj-lh-transition-01/confirm", json={"reviewer_id": "usr-1"})
        assert res_conf_lh.status_code == 400
        assert "legal hold" in res_conf_lh.json()["detail"].lower()

        # Override transition on legal hold object -> 400 Bad Request
        res_ovr_lh = client.post("/api/v1/recommendations/obj-lh-transition-01/override", json={
            "reviewer_id": "usr-1",
            "override_reason": "PENDING_CLINICAL_TRIAL"
        })
        assert res_ovr_lh.status_code == 400
        assert "legal hold" in res_ovr_lh.json()["detail"].lower()

        # 2. Ingest legal_hold=True object with NO_ACTION recommendation (e.g. MEDICAL_IMAGE under retention)
        client.post("/api/v1/objects", json={
            "id": "obj-lh-noaction-01",
            "bucket_or_account": "b-legal-hold",
            "cloud_provider": "AWS",
            "data_classification": "MEDICAL_IMAGE",
            "current_storage_class": "HOT",
            "size_bytes": 1000,
            "object_age_days": 100,
            "legal_hold": True
        })
        client.get("/api/v1/recommendations")

        # Confirm NO_ACTION on legal hold object -> 200 OK
        res_conf_noaction = client.post("/api/v1/recommendations/obj-lh-noaction-01/confirm", json={"reviewer_id": "usr-1"})
        assert res_conf_noaction.status_code == 200
        assert res_conf_noaction.json()["approval_status"] == "confirmed"

        # 3. Ingest object qualifying for DELETE recommendation (e.g., APP_LOG object age 400 days > max_lifecycle 365)
        client.post("/api/v1/objects", json={
            "id": "obj-delete-01",
            "bucket_or_account": "b-delete",
            "cloud_provider": "AWS",
            "data_classification": "APP_LOG",
            "current_storage_class": "HOT",
            "size_bytes": 1000,
            "object_age_days": 400,
            "legal_hold": False
        })
        client.get("/api/v1/recommendations")

        # Confirm DELETE without justification -> 400 Bad Request
        res_conf_del_no_just = client.post("/api/v1/recommendations/obj-delete-01/confirm", json={"reviewer_id": "usr-1"})
        assert res_conf_del_no_just.status_code == 400
        assert "justification" in res_conf_del_no_just.json()["detail"].lower()

        # Confirm DELETE with whitespace-only justification -> 400 Bad Request
        res_conf_del_blank_just = client.post("/api/v1/recommendations/obj-delete-01/confirm", json={
            "reviewer_id": "usr-1",
            "justification": "   "
        })
        assert res_conf_del_blank_just.status_code == 400
        assert "justification" in res_conf_del_blank_just.json()["detail"].lower()

        # Confirm DELETE with valid justification -> 200 OK
        res_conf_del_ok = client.post("/api/v1/recommendations/obj-delete-01/confirm", json={
            "reviewer_id": "usr-1",
            "justification": "Approved for deletion as temporary scratch data is expired"
        })
        assert res_conf_del_ok.status_code == 200
        assert res_conf_del_ok.json()["approval_status"] == "confirmed"


def test_idempotent_ensure_indexes_and_reinit(tmp_path):
    """
    Verifies that init_db() and ensure_indexes() run idempotently on existing tables
    without raising errors on repeated initialization.
    """
    test_db_file = os.path.join(tmp_path, "test_indexes.db")
    test_engine = create_engine(f"sqlite:///{test_db_file}")

    # First initialization creates tables and indexes
    init_db(target_engine=test_engine)

    # Secondary initialization on existing database must succeed without errors
    init_db(target_engine=test_engine)
    ensure_indexes(target_engine=test_engine)

    # Inspect index names on created tables
    from sqlalchemy import inspect
    inspector = inspect(test_engine)

    audit_indexes = [idx["name"] for idx in inspector.get_indexes("audit_log")]
    rec_indexes = [idx["name"] for idx in inspector.get_indexes("recommendations")]
    conf_indexes = [idx["name"] for idx in inspector.get_indexes("confirmations_and_overrides")]

    assert "ix_audit_log_object_id" in audit_indexes
    assert "ix_recommendations_approval_status" in rec_indexes
    assert "ix_confirmations_and_overrides_object_id" in conf_indexes

    test_engine.dispose()


def test_auth_rbac_permissions(monkeypatch):
    """
    Verifies X-API-Key authentication and role-based access control (RBAC):
    - 401 Unauthorized when API_KEY is set and no or invalid X-API-Key is provided.
    - Viewer role can read GET endpoints, but POST operations return 403 Forbidden.
    - Reviewer role can confirm/override recommendations, but ingest/rollback return 403.
    - Admin role can execute all operations.
    """
    monkeypatch.setenv("API_KEY", "key-admin-123")
    monkeypatch.setenv("API_KEY_REVIEWER", "key-reviewer-456")
    monkeypatch.setenv("API_KEY_VIEWER", "key-viewer-789")

    with TestClient(app) as client:
        # 1. Unauthenticated request -> 401 Unauthorized
        res_no_auth = client.get("/api/v1/recommendations")
        assert res_no_auth.status_code == 401

        res_bad_auth = client.get("/api/v1/recommendations", headers={"X-API-Key": "wrong-key"})
        assert res_bad_auth.status_code == 401

        # 2. Viewer role
        headers_viewer = {"X-API-Key": "key-viewer-789"}
        res_view = client.get("/api/v1/recommendations", headers=headers_viewer)
        assert res_view.status_code == 200

        res_view_ingest = client.post("/api/v1/objects", json={
            "id": "obj-auth-01",
            "bucket_or_account": "b-auth",
            "cloud_provider": "AWS",
            "data_classification": "BACKUP",
            "current_storage_class": "HOT",
            "size_bytes": 1000,
            "object_age_days": 100,
            "legal_hold": False
        }, headers=headers_viewer)
        assert res_view_ingest.status_code == 403

        # 3. Reviewer role
        headers_reviewer = {"X-API-Key": "key-reviewer-456"}
        headers_admin = {"X-API-Key": "key-admin-123"}

        # Admin ingests object
        res_admin_ingest = client.post("/api/v1/objects", json={
            "id": "obj-auth-01",
            "bucket_or_account": "b-auth",
            "cloud_provider": "AWS",
            "data_classification": "BACKUP",
            "current_storage_class": "HOT",
            "size_bytes": 1000,
            "object_age_days": 100,
            "legal_hold": False
        }, headers=headers_admin)
        assert res_admin_ingest.status_code == 201

        # Sync recs
        client.get("/api/v1/recommendations", headers=headers_reviewer)

        # Reviewer can confirm
        res_rev_conf = client.post("/api/v1/recommendations/obj-auth-01/confirm", json={"reviewer_id": "usr-reviewer"}, headers=headers_reviewer)
        assert res_rev_conf.status_code == 200

        # Reviewer CANNOT rollback -> 403 Forbidden
        res_rev_rlb = client.post("/api/v1/recommendations/obj-auth-01/rollback", json={"reviewer_id": "usr-reviewer", "reason": "test"}, headers=headers_reviewer)
        assert res_rev_rlb.status_code == 403

        # Admin CAN rollback -> 200 OK
        res_admin_rlb = client.post("/api/v1/recommendations/obj-auth-01/rollback", json={"reviewer_id": "usr-admin", "reason": "test"}, headers=headers_admin)
        assert res_admin_rlb.status_code == 200
        assert res_admin_rlb.json()["approval_status"] == "rolled_back"


def test_dev_keys_disabled_when_env_keys_set(monkeypatch):
    """
    FIX 1: DEFAULT_KEYS (dev-admin-key, etc.) must NEVER be accepted when any environment API key is set.
    """
    monkeypatch.setenv("API_KEY", "real-prod-admin-key")
    monkeypatch.delenv("API_KEY_REVIEWER", raising=False)
    monkeypatch.delenv("API_KEY_VIEWER", raising=False)
    monkeypatch.delenv("ALLOW_DEV_KEYS", raising=False)

    with TestClient(app) as client:
        # Attempting to use developer fallback key must return 401 Unauthorized
        res_dev_key = client.get("/api/v1/recommendations", headers={"X-API-Key": "dev-admin-key"})
        assert res_dev_key.status_code == 401

        # Using real production key succeeds
        res_prod_key = client.get("/api/v1/recommendations", headers={"X-API-Key": "real-prod-admin-key"})
        assert res_prod_key.status_code == 200


def test_render_missing_api_key_raises_runtime_error(monkeypatch):
    """
    FIX 1: On Render (RENDER=true), if no API key env vars are set, initializing/verifying auth config raises RuntimeError.
    """
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("API_KEY_REVIEWER", raising=False)
    monkeypatch.delenv("API_KEY_VIEWER", raising=False)
    monkeypatch.delenv("ALLOW_DEV_KEYS", raising=False)

    from src.api.auth import verify_api_key_config
    with pytest.raises(RuntimeError, match="API key configuration .* is strictly required when running on Render"):
        verify_api_key_config()









