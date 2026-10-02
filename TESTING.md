# Testing Architecture & Safety Verification Guide

This document describes the testing strategy, cloud mocking architecture, failure boundaries, and regression test matrix for the **Hospital Storage Lifecycle Recommender**.

---

## 1. Testing Philosophy & Isolation Policy

1. **Zero Live Cloud Calls in CI**: Automated test suites must NEVER incur financial costs, touch real AWS S3 credentials, or mutate external infrastructure. All cloud interactions are hermetically simulated using `moto[s3]`.
2. **Cryptographic Integrity by Default**: Every lifecycle event (ingestion, confirmation, override, rollback, execution, and legal-hold rejections) is written to a SHA-256 hash-chained audit log. Tests verify `verify_audit_chain` validity across all workflows.
3. **Failure Boundary Hardening**: Tests actively exercise negative paths (400, 401, 403, 404, 409, 500) to ensure the system fails closed and preserves audit trails under error conditions.

---

## 2. Cloud Mocking Strategy (`moto[s3]`)

### Fixture Architecture
AWS S3 interactions are mocked using Moto's `@mock_aws` context manager or the `aws_env` fixture (`monkeypatch`):
```python
@pytest.fixture
def aws_env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "mock-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "mock-secret")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
```

### In-Place S3 Transitions
AWS S3 does not provide a native "change storage tier" API call; tier modification is achieved via `s3.copy_object` with `StorageClass` metadata pointing to the same key (`CopySource={'Bucket': bucket, 'Key': key}`).
- Tests verify that `CopyObject` properly sets the target tier (`STANDARD_IA`, `GLACIER`, `DEEP_ARCHIVE`).
- Tests verify `HeadObject` returns the updated `StorageClass`.
- Tests assert that non-existent S3 objects raise `ObjectNotFoundError` (HTTP 404) without modifying local database records.

---

## 3. Failure Boundaries & Safety Gates

### 1. Authentication & RBAC Boundaries
- **Missing / Invalid Key**: Header `X-API-Key` missing or unknown returns `HTTP 401 Unauthorized`.
- **Role Permissions**: Read-only `viewer` key cannot POST or confirm (returns `HTTP 403 Forbidden`). Only `admin` can ingest objects, execute transitions, or run batch operations.
- **Reviewer Identity Anti-Spoofing**: If a client sends a body containing `reviewer_id: "other_user"`, the backend strictly overrides or rejects it in favor of `user.user_id` derived directly from the authenticated API key context.

### 2. Legal Hold Race Condition Gates
- **Double-Check Policy**: Legal hold status is validated twice:
  1. At **Confirmation** time (HTTP 400 rejection).
  2. At **Execution** time (HTTP 400 rejection). If an object is marked under legal hold between review and physical transition, execution is halted immediately.
- **Audit Survival**: Even when an operation is rejected with `HTTP 400`, the rejection event (`REJECT_LEGAL_HOLD_VIOLATION` or `EXECUTION_BLOCKED_LEGAL_HOLD`) is committed to the hash-chained audit trail before the exception is raised.

### 3. Irreversible Action Safeguards
- S3 `DELETE` and transitions to `DEEP_ARCHIVE` (which incurs multi-day retrieval latency and high retrieval cost) are classified as **irreversible**.
- Single-object execution requires `confirm_irreversible: true` and a non-empty `irreversible_justification`.
- Batch execution automatically skips unconfirmed irreversible actions (`skipped_irreversible_count`) unless explicitly authorized in the batch request payload.

### 4. Cloud Provider API Failures & Partial States
- When S3 raises a `ClientError` (network failure, 404 Not Found, 403 Access Denied from provider):
  - Recommendation status is updated to `execution_failed`.
  - An `EXECUTION_FAILED` audit entry is recorded with failure details and error code.
  - The system **never** marks the record as `executed` when the cloud call fails.
  - Deletions are never silently retried.

### 5. Database Concurrency & Chain Fork Prevention
- **PostgreSQL**: Handlers acquire `get_recommendation_with_lock` (row-level `FOR UPDATE`) and `pg_advisory_xact_lock(74294101)` inside the transaction. Two concurrent rollbacks serialize cleanly (one returns 200, the other 409).
- **SQLite (Dev / Local)**: Evaluates single-process workflows. Tests verify graceful fallback without crashes.

---

## 4. Test Suite Matrix

| Test Module | Tests | Target Scope & Invariants Tested |
| :--- | :---: | :--- |
| `tests/test_rules_engine.py` | 6 | Deterministic rule matching, medical data classifications (DICOM, EHR, BACKUP, LOG), retention calculations, legal hold blocks. |
| `tests/test_api.py` | 5 | API routes, object ingestion, recommendation generation, filter parameters, Swagger documentation. |
| `tests/test_phase2_governance.py` | 21 | RBAC (401/403), reviewer anti-spoofing across 4 endpoints, legal hold denials, audit hash chain verification, advisory lock handling. |
| `tests/test_cost_service.py` | 3 | Real AWS S3 pricing calculations ($0.023/GB HOT, $0.0125/GB COOL, $0.004/GB COLD, $0.0036/GB ARCHIVE, $0.00099/GB DEEP_ARCHIVE), cost report aggregation. |
| `tests/test_cloud_executor.py` | 4 | Moto S3 executor, `CopyObject` in-place storage tier transitions, `DeleteObject`, 404 object not found exception mapping. |
| `tests/test_execution_service.py` | 5 | Single recommendation execution, legal hold execution-time blocking, irreversible deletion confirmation, provider error handling. |
| `tests/test_batch_runner.py` | 3 | Scheduled batch runner, `dry_run` preview mode, bucket filtering, automated skipping of irreversible actions, audit trail generation. |

**Total Test Count**: 47 automated tests (45 executed in standard local suite, 2 PostgreSQL advisory lock concurrency tests executed in CI service container).

---

## 5. Running the Test Suite Locally

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run full pytest suite
pytest -v

# 3. Run specific test modules
pytest tests/test_execution_service.py -v
pytest tests/test_batch_runner.py -v
pytest tests/test_cost_service.py -v
```
