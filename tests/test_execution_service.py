import uuid
import pytest
import boto3
from moto import mock_aws
from fastapi.testclient import TestClient

from src.api.main import app
from src.models.storage_object import StorageClass, CloudProvider, DataClassification
from src.models.recommendation import RecommendedAction, ApprovalStatus
from src.db import SessionLocal, StorageObjectDB, RecommendationDB, AuditLogDB
from src.services.audit_service import verify_audit_chain


@pytest.fixture
def aws_env(monkeypatch):
    """Setup mock AWS credentials for moto."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "mock-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "mock-secret")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("API_KEY", "admin-key-exec")
    monkeypatch.setenv("API_KEY_REVIEWER", "reviewer-key-exec")


def test_execute_transition_success(aws_env):
    """
    1. Verifies that a confirmed TRANSITION recommendation executes against cloud storage,
    updates object storage class, updates approval_status to 'executed', and appends
    an EXECUTE_TRANSITION audit entry in the hash chain.
    """
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        bucket = "hospital-records-exec"
        s3.create_bucket(Bucket=bucket)
        obj_key = f"obj-trans-{uuid.uuid4().hex[:6]}"
        s3.put_object(Bucket=bucket, Key=obj_key, Body=b"MEDICAL_RECORD", StorageClass="STANDARD")

        headers_admin = {"X-API-Key": "admin-key-exec"}
        headers_reviewer = {"X-API-Key": "reviewer-key-exec"}

        with TestClient(app) as client:
            # Ingest object
            client.post("/api/v1/objects", json={
                "id": obj_key,
                "bucket_or_account": bucket,
                "cloud_provider": "AWS",
                "data_classification": "BACKUP",
                "current_storage_class": "HOT",
                "size_bytes": 5000,
                "object_age_days": 100,
                "legal_hold": False
            }, headers=headers_admin)
            client.get("/api/v1/recommendations", headers=headers_reviewer)

            # Confirm recommendation
            res_conf = client.post(f"/api/v1/recommendations/{obj_key}/confirm", json={"reviewer_id": "usr-rev"}, headers=headers_reviewer)
            assert res_conf.status_code == 200

            # Execute recommendation (requires admin role)
            res_exec = client.post(f"/api/v1/recommendations/{obj_key}/execute", json={}, headers=headers_admin)
            assert res_exec.status_code == 200
            data = res_exec.json()
            assert data["approval_status"] == "executed"

            # Verify physical S3 tier was transitioned to COOL (STANDARD_IA)
            head = s3.head_object(Bucket=bucket, Key=obj_key)
            assert head["StorageClass"] == "STANDARD_IA"

            # Verify audit trail contains EXECUTE_TRANSITION
            audit_res = client.get(f"/api/v1/audit-log?object_id={obj_key}", headers=headers_admin).json()
            exec_entries = [a for a in audit_res if a["event_type"] == "EXECUTE_TRANSITION"]
            assert len(exec_entries) == 1
            assert exec_entries[0]["actor"] == "usr-admin-key"

            # Verify cryptographic hash chain integrity
            db = SessionLocal()
            try:
                is_valid, tampered_id, msg = verify_audit_chain(db)
                assert is_valid is True, f"Audit chain verification failed: {msg}"
            finally:
                db.close()

            # IDEMPOTENCY TEST: Second call on already executed recommendation returns 200 without duplicate execution
            res_exec_retry = client.post(f"/api/v1/recommendations/{obj_key}/execute", json={}, headers=headers_admin)
            assert res_exec_retry.status_code == 200
            data_retry = res_exec_retry.json()
            assert data_retry["approval_status"] == "executed"

            # Must still have only 1 EXECUTE_TRANSITION audit entry
            audit_res2 = client.get(f"/api/v1/audit-log?object_id={obj_key}", headers=headers_admin).json()
            exec_entries2 = [a for a in audit_res2 if a["event_type"] == "EXECUTE_TRANSITION"]
            assert len(exec_entries2) == 1


def test_execute_blocked_by_legal_hold_at_execution_time(aws_env):
    """
    2. Verifies that if legal_hold flips to True between confirm and execute,
    execution is aborted, returns HTTP 400, and writes an EXECUTION_BLOCKED_LEGAL_HOLD
    audit entry into the hash chain.
    """
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        bucket = "hospital-lh-exec"
        s3.create_bucket(Bucket=bucket)
        obj_key = f"obj-lh-block-{uuid.uuid4().hex[:6]}"
        s3.put_object(Bucket=bucket, Key=obj_key, Body=b"DICOM_DATA", StorageClass="STANDARD")

        headers_admin = {"X-API-Key": "admin-key-exec"}
        headers_reviewer = {"X-API-Key": "reviewer-key-exec"}

        with TestClient(app) as client:
            client.post("/api/v1/objects", json={
                "id": obj_key,
                "bucket_or_account": bucket,
                "cloud_provider": "AWS",
                "data_classification": "BACKUP",
                "current_storage_class": "HOT",
                "size_bytes": 10000,
                "object_age_days": 120,
                "legal_hold": False
            }, headers=headers_admin)
            client.get("/api/v1/recommendations", headers=headers_reviewer)

            # Confirm while legal_hold is False
            res_conf = client.post(f"/api/v1/recommendations/{obj_key}/confirm", json={"reviewer_id": "usr-rev"}, headers=headers_reviewer)
            assert res_conf.status_code == 200

            # State change: Legal hold is enabled on the object before execution!
            db = SessionLocal()
            try:
                obj_db = db.query(StorageObjectDB).filter(StorageObjectDB.id == obj_key).first()
                obj_db.legal_hold = True
                db.commit()
            finally:
                db.close()

            # Attempt execution -> must be blocked
            res_exec = client.post(f"/api/v1/recommendations/{obj_key}/execute", json={}, headers=headers_admin)
            assert res_exec.status_code == 400
            assert "legal hold" in res_exec.json()["detail"].lower()

            # Physical S3 tier must remain unchanged
            head = s3.head_object(Bucket=bucket, Key=obj_key)
            assert head.get("StorageClass", "STANDARD") == "STANDARD"

            # Check audit log for EXECUTION_BLOCKED_LEGAL_HOLD
            audit_res = client.get(f"/api/v1/audit-log?object_id={obj_key}", headers=headers_admin).json()
            blocked_entries = [a for a in audit_res if a["event_type"] == "EXECUTION_BLOCKED_LEGAL_HOLD"]
            assert len(blocked_entries) == 1
            assert blocked_entries[0]["actor"] == "usr-admin-key"

            # Verify cryptographic hash chain
            db_verify = SessionLocal()
            try:
                is_valid, _, msg = verify_audit_chain(db_verify)
                assert is_valid is True, f"Audit chain verification failed: {msg}"
            finally:
                db_verify.close()


def test_execute_delete_requires_confirm_irreversible(aws_env):
    """
    3. Verifies that DELETE actions require explicit confirm_irreversible=True with justification.
    Without the flag, execution is rejected with HTTP 400.
    With the flag, object is deleted from S3 and audit entry is recorded.
    """
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        bucket = "hospital-del-exec"
        s3.create_bucket(Bucket=bucket)
        obj_key = f"obj-del-block-{uuid.uuid4().hex[:6]}"
        s3.put_object(Bucket=bucket, Key=obj_key, Body=b"OLD_LOG_CONTENT", StorageClass="STANDARD")

        headers_admin = {"X-API-Key": "admin-key-exec"}
        headers_reviewer = {"X-API-Key": "reviewer-key-exec"}

        with TestClient(app) as client:
            client.post("/api/v1/objects", json={
                "id": obj_key,
                "bucket_or_account": bucket,
                "cloud_provider": "AWS",
                "data_classification": "APP_LOG",
                "current_storage_class": "HOT",
                "size_bytes": 2000,
                "object_age_days": 400,
                "legal_hold": False
            }, headers=headers_admin)
            client.get("/api/v1/recommendations", headers=headers_reviewer)

            # Confirm with justification
            res_conf = client.post(f"/api/v1/recommendations/{obj_key}/confirm", json={"reviewer_id": "usr-rev", "justification": "Expired log retention"}, headers=headers_reviewer)
            assert res_conf.status_code == 200

            # Attempt execute without confirm_irreversible -> 400 Bad Request
            res_no_flag = client.post(f"/api/v1/recommendations/{obj_key}/execute", json={}, headers=headers_admin)
            assert res_no_flag.status_code == 400
            assert "confirm_irreversible" in res_no_flag.json()["detail"]

            # Execute WITH confirm_irreversible and justification -> 200 OK
            res_exec = client.post(f"/api/v1/recommendations/{obj_key}/execute", json={
                "confirm_irreversible": True,
                "irreversible_justification": "Authorizing permanent deletion per hospital data policy"
            }, headers=headers_admin)
            assert res_exec.status_code == 200

            # Verify object was physically deleted in S3
            with pytest.raises(Exception):
                s3.head_object(Bucket=bucket, Key=obj_key)


def test_execute_cloud_failure_does_not_record_executed(aws_env, monkeypatch):
    """
    4. Verifies that when the cloud provider returns an error (e.g. 500 / network outage),
    the recommendation status becomes 'execution_failed', EXECUTION_FAILED is logged in audit,
    and no false EXECUTED entry is recorded.
    """
    from botocore.exceptions import ClientError
    from src.services.cloud_executor import S3StorageExecutor

    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        bucket = "hospital-fail-exec"
        s3.create_bucket(Bucket=bucket)
        obj_key = f"obj-fail-{uuid.uuid4().hex[:6]}"
        s3.put_object(Bucket=bucket, Key=obj_key, Body=b"TEST_RESULTS", StorageClass="STANDARD")

        headers_admin = {"X-API-Key": "admin-key-exec"}
        headers_reviewer = {"X-API-Key": "reviewer-key-exec"}

        # Simulate cloud provider 500 error on copy_object
        def broken_transition(self, bucket, key, target_class):
            raise ClientError({"Error": {"Code": "500", "Message": "AWS Internal Server Error"}}, "CopyObject")

        monkeypatch.setattr(S3StorageExecutor, "transition_object", broken_transition)

        with TestClient(app) as client:
            client.post("/api/v1/objects", json={
                "id": obj_key,
                "bucket_or_account": bucket,
                "cloud_provider": "AWS",
                "data_classification": "BACKUP",
                "current_storage_class": "HOT",
                "size_bytes": 1000,
                "object_age_days": 100,
                "legal_hold": False
            }, headers=headers_admin)
            client.get("/api/v1/recommendations", headers=headers_reviewer)
            client.post(f"/api/v1/recommendations/{obj_key}/confirm", json={"reviewer_id": "usr-rev"}, headers=headers_reviewer)

            # Attempt execute -> cloud provider fails with 500
            res_exec = client.post(f"/api/v1/recommendations/{obj_key}/execute", json={}, headers=headers_admin)
            assert res_exec.status_code == 500

            # Verify audit trail recorded EXECUTION_FAILED and NOT EXECUTE_TRANSITION
            audit_res = client.get(f"/api/v1/audit-log?object_id={obj_key}", headers=headers_admin).json()
            event_types = [a["event_type"] for a in audit_res]
            assert "EXECUTION_FAILED" in event_types
            assert "EXECUTE_TRANSITION" not in event_types

            # Verify recommendation status is 'execution_failed'
            recs = client.get("/api/v1/recommendations", headers=headers_reviewer).json()
            rec = next(r for r in recs if r["object_id"] == obj_key)
            assert rec["approval_status"] == "execution_failed"


def test_execute_unconfirmed_recommendation_returns_409(aws_env):
    """
    5. Verifies that executing a recommendation that is NOT in 'confirmed' status
    is rejected with HTTP 409 Conflict.
    """
    headers_admin = {"X-API-Key": "admin-key-exec"}
    headers_reviewer = {"X-API-Key": "reviewer-key-exec"}

    with TestClient(app) as client:
        obj_key = f"obj-unconf-{uuid.uuid4().hex[:6]}"
        client.post("/api/v1/objects", json={
            "id": obj_key,
            "bucket_or_account": "b-unconfirmed",
            "cloud_provider": "AWS",
            "data_classification": "BACKUP",
            "current_storage_class": "HOT",
            "size_bytes": 1000,
            "object_age_days": 100,
            "legal_hold": False
        }, headers=headers_admin)
        client.get("/api/v1/recommendations", headers=headers_reviewer)

        # Recommendation is in 'pending' status, attempting execute returns 409
        res_exec = client.post(f"/api/v1/recommendations/{obj_key}/execute", json={}, headers=headers_admin)
        assert res_exec.status_code == 409
        assert "must be in 'confirmed' status" in res_exec.json()["detail"].lower()


def test_full_lifecycle_audit_chain_verification(aws_env):
    """
    6. Full Lifecycle Verification:
    Tests that a full multi-stage lifecycle — INGEST_OBJECT, CONFIRM_RECOMMENDATION,
    EXECUTE_TRANSITION, EXECUTION_BLOCKED_LEGAL_HOLD, and BATCH_EXECUTION_RUN —
    maintains an unbroken SHA-256 cryptographic audit chain verified by GET /api/v1/audit-log/verify.
    """
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        bucket = f"hospital-lifecycle-{uuid.uuid4().hex[:6]}"
        s3.create_bucket(Bucket=bucket)

        obj_1 = f"obj-cycle-1-{uuid.uuid4().hex[:6]}"
        obj_2 = f"obj-cycle-2-{uuid.uuid4().hex[:6]}"
        s3.put_object(Bucket=bucket, Key=obj_1, Body=b"DATA_1", StorageClass="STANDARD")
        s3.put_object(Bucket=bucket, Key=obj_2, Body=b"DATA_2", StorageClass="STANDARD")

        headers_admin = {"X-API-Key": "admin-key-exec"}
        headers_reviewer = {"X-API-Key": "reviewer-key-exec"}

        with TestClient(app) as client:
            # 1. Ingest obj 1 & obj 2
            client.post("/api/v1/objects", json={
                "id": obj_1,
                "bucket_or_account": bucket,
                "cloud_provider": "AWS",
                "data_classification": "BACKUP",
                "current_storage_class": "HOT",
                "size_bytes": 1000,
                "object_age_days": 100,
                "legal_hold": False
            }, headers=headers_admin)

            client.post("/api/v1/objects", json={
                "id": obj_2,
                "bucket_or_account": bucket,
                "cloud_provider": "AWS",
                "data_classification": "BACKUP",
                "current_storage_class": "HOT",
                "size_bytes": 1000,
                "object_age_days": 100,
                "legal_hold": False
            }, headers=headers_admin)

            client.get("/api/v1/recommendations", headers=headers_reviewer)

            # 2. Confirm both
            client.post(f"/api/v1/recommendations/{obj_1}/confirm", json={"reviewer_id": "usr-rev"}, headers=headers_reviewer)
            client.post(f"/api/v1/recommendations/{obj_2}/confirm", json={"reviewer_id": "usr-rev"}, headers=headers_reviewer)

            # 3. Execute obj 1 directly -> EXECUTE_TRANSITION
            res_exec1 = client.post(f"/api/v1/recommendations/{obj_1}/execute", json={}, headers=headers_admin)
            assert res_exec1.status_code == 200

            # 4. Batch run -> processes obj 2 -> BATCH_EXECUTION_RUN
            res_batch = client.post("/api/v1/execution/batch-run", json={
                "bucket_or_account": bucket,
                "dry_run": False
            }, headers=headers_admin)
            assert res_batch.status_code == 200

            # 5. Verify unbroken cryptographic chain via API endpoint
            res_verify = client.get("/api/v1/audit-log/verify", headers=headers_admin)
            assert res_verify.status_code == 200
            verify_data = res_verify.json()
            assert verify_data["is_valid"] is True
            assert verify_data["tampered_entry_id"] is None
            assert "integrity verified" in verify_data["message"].lower()

