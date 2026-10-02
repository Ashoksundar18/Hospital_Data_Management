import uuid
import pytest
import boto3
from moto import mock_aws
from fastapi.testclient import TestClient

from src.api.main import app
from src.db import SessionLocal
from src.services.audit_service import verify_audit_chain


@pytest.fixture
def aws_env(monkeypatch):
    """Setup mock AWS credentials for moto."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "mock-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "mock-secret")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("API_KEY", "admin-key-batch")
    monkeypatch.setenv("API_KEY_REVIEWER", "reviewer-key-batch")


def test_batch_execution_dry_run(aws_env):
    """
    1. Verifies that dry_run=True previews confirmed recommendations
    without transitioning physical S3 objects or setting status to 'executed'.
    """
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        bucket = f"hospital-batch-dryrun-{uuid.uuid4().hex[:6]}"
        s3.create_bucket(Bucket=bucket)
        obj_key = f"obj-dry-{uuid.uuid4().hex[:6]}"
        s3.put_object(Bucket=bucket, Key=obj_key, Body=b"DATA", StorageClass="STANDARD")

        headers_admin = {"X-API-Key": "admin-key-batch"}
        headers_reviewer = {"X-API-Key": "reviewer-key-batch"}

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

            # Trigger batch run in dry_run mode
            res_batch = client.post("/api/v1/execution/batch-run", json={
                "bucket_or_account": bucket,
                "dry_run": True,
                "max_items": 10
            }, headers=headers_admin)
            assert res_batch.status_code == 200
            data = res_batch.json()
            assert data["dry_run"] is True
            assert data["total_candidates"] == 1
            assert data["executed_count"] == 0

            # S3 physical object must remain in STANDARD tier
            head = s3.head_object(Bucket=bucket, Key=obj_key)
            assert head.get("StorageClass", "STANDARD") == "STANDARD"

            # Recommendation status remains 'confirmed' (not 'executed')
            recs = client.get("/api/v1/recommendations", headers=headers_reviewer).json()
            rec = next(r for r in recs if r["object_id"] == obj_key)
            assert rec["approval_status"] == "confirmed"


def test_batch_execution_full_run(aws_env):
    """
    2. Verifies that dry_run=False executes confirmed recommendations,
    transitions S3 storage classes, marks recommendations 'executed', and logs BATCH_EXECUTION_RUN in audit.
    """
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        bucket = f"hospital-batch-full-{uuid.uuid4().hex[:6]}"
        s3.create_bucket(Bucket=bucket)
        obj_key = f"obj-full-{uuid.uuid4().hex[:6]}"
        s3.put_object(Bucket=bucket, Key=obj_key, Body=b"DATA", StorageClass="STANDARD")

        headers_admin = {"X-API-Key": "admin-key-batch"}
        headers_reviewer = {"X-API-Key": "reviewer-key-batch"}

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

            # Trigger real batch execution
            res_batch = client.post("/api/v1/execution/batch-run", json={
                "bucket_or_account": bucket,
                "dry_run": False,
                "max_items": 10
            }, headers=headers_admin)
            assert res_batch.status_code == 200
            data = res_batch.json()
            assert data["dry_run"] is False
            assert data["executed_count"] == 1

            # Physical S3 object transitioned to COOL (STANDARD_IA)
            head = s3.head_object(Bucket=bucket, Key=obj_key)
            assert head["StorageClass"] == "STANDARD_IA"

            # Check audit log for BATCH_EXECUTION_RUN
            audit_res = client.get("/api/v1/audit-log?event_type=BATCH_EXECUTION_RUN", headers=headers_admin).json()
            assert len(audit_res) >= 1
            assert audit_res[-1]["actor"] == "usr-admin-key"

            # Verify audit chain integrity
            db = SessionLocal()
            try:
                is_valid, _, msg = verify_audit_chain(db)
                assert is_valid is True, f"Audit chain verification failed: {msg}"
            finally:
                db.close()


def test_batch_execution_blocks_legal_hold_and_skips_unconfirmed_irreversible(aws_env):
    """
    3. Verifies that batch execution automatically blocks legal hold objects
    and skips irreversible DELETE objects when confirm_irreversible is not provided.
    """
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        bucket = f"hospital-batch-safeguards-{uuid.uuid4().hex[:6]}"
        s3.create_bucket(Bucket=bucket)

        obj_lh = f"obj-lh-{uuid.uuid4().hex[:6]}"
        obj_del = f"obj-del-{uuid.uuid4().hex[:6]}"

        s3.put_object(Bucket=bucket, Key=obj_lh, Body=b"DATA", StorageClass="STANDARD")
        s3.put_object(Bucket=bucket, Key=obj_del, Body=b"DATA", StorageClass="STANDARD")

        headers_admin = {"X-API-Key": "admin-key-batch"}
        headers_reviewer = {"X-API-Key": "reviewer-key-batch"}

        with TestClient(app) as client:
            # Ingest legal hold object (age 100 -> TRANSITION COOL)
            client.post("/api/v1/objects", json={
                "id": obj_lh,
                "bucket_or_account": bucket,
                "cloud_provider": "AWS",
                "data_classification": "BACKUP",
                "current_storage_class": "HOT",
                "size_bytes": 1000,
                "object_age_days": 100,
                "legal_hold": False
            }, headers=headers_admin)

            # Ingest DELETE object (APP_LOG age 400 -> DELETE)
            client.post("/api/v1/objects", json={
                "id": obj_del,
                "bucket_or_account": bucket,
                "cloud_provider": "AWS",
                "data_classification": "APP_LOG",
                "current_storage_class": "HOT",
                "size_bytes": 1000,
                "object_age_days": 400,
                "legal_hold": False
            }, headers=headers_admin)

            client.get("/api/v1/recommendations", headers=headers_reviewer)

            # Confirm both
            client.post(f"/api/v1/recommendations/{obj_lh}/confirm", json={"reviewer_id": "usr-rev"}, headers=headers_reviewer)
            client.post(f"/api/v1/recommendations/{obj_del}/confirm", json={"reviewer_id": "usr-rev", "justification": "Expired log retention"}, headers=headers_reviewer)

            # Enable legal hold on obj_lh
            from src.db import StorageObjectDB
            db = SessionLocal()
            try:
                db_obj = db.query(StorageObjectDB).filter(StorageObjectDB.id == obj_lh).first()
                db_obj.legal_hold = True
                db.commit()
            finally:
                db.close()

            # Execute batch WITHOUT confirm_irreversible
            res_batch = client.post("/api/v1/execution/batch-run", json={
                "bucket_or_account": bucket,
                "dry_run": False,
                "confirm_irreversible": False
            }, headers=headers_admin)
            assert res_batch.status_code == 200
            data = res_batch.json()

            assert data["blocked_by_legal_hold_count"] == 1
            assert data["skipped_irreversible_count"] == 1
            assert data["executed_count"] == 0

