# Hospital Storage Lifecycle Recommender

[![CI & Governance Tests](https://github.com/Ashoksundar18/Hospital_Data_Management/actions/workflows/ci.yml/badge.svg)](https://github.com/Ashoksundar18/Hospital_Data_Management/actions/workflows/ci.yml)
[![Live on Render](https://img.shields.io/badge/Render-Hosted%20Live-brightgreen)](https://hospital-storage-recommender.onrender.com)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

An enterprise-grade, human-governed storage lifecycle recommendation and automated transition platform built for hospital networks. It optimizes multi-tier cloud storage costs (AWS S3) for medical imaging (DICOM), electronic health records (EHR), database backups, and audit logs while enforcing strict clinical retrieval SLAs, legal holds, and cryptographic tamper-evident audit logging.

---

## 🏗️ System Architecture & Phase Overview

```mermaid
flowchart TD
    Obj["Medical Object (DICOM/EHR/LOG)"] --> Ingest["Ingestion API\n(POST /api/v1/objects)"]
    Ingest --> Engine["Deterministic Rules Engine\n(7 Clinical & SLA Rules)"]
    Engine --> Rec["Recommendation Generated\n(approval_status: pending)"]
    
    subgraph Phase 2: Governance & Cryptographic Chain
        Rec --> Review["Human-in-the-Loop Review\n(POST /confirm or /override)"]
        Review --> PreAudit["Legal Hold & SLA Check"]
        PreAudit --> AuditLog["SHA-256 Hash Chain\n(pg_advisory_xact_lock)"]
        AuditLog --> ConfRec["Status: Confirmed"]
    end

    subgraph Phase 3: Cloud Execution & Cost Engine
        ConfRec --> ExecGate["Execution Safety Gate\n(Re-check Legal Hold + Irreversible Safeguards)"]
        ExecGate -->|Approved| S3Exec["S3 Cloud Storage Executor\n(In-Place CopyObject / Delete)"]
        ExecGate -->|Blocked| BlockAudit["Log EXECUTION_BLOCKED_LEGAL_HOLD"]
        S3Exec --> ExecAudit["Audit Trail: EXECUTED / EXECUTION_FAILED"]
        S3Exec --> Cost["Cost Engine\n(Real AWS S3 Pricing Report)"]
    end
```

- **Phase 1: Deterministic Rules Engine**: Evaluates retention policies, clinical access frequency, data classifications, and retrieval SLAs to issue recommendations with rule-scoped confidence and evidence strings.
- **Phase 2: Governance & Cryptographic Audit Trail**: Human-in-the-loop confirmation, override taxonomies, rollback mechanisms, role-based access control (RBAC), anti-spoofing reviewer binding, and SHA-256 hash-chained tamper-evident audit logging.
- **Phase 3: Automated Cloud Transitions & Cost Reporting**: Automated AWS S3 tier migration (`STANDARD`, `STANDARD_IA`, `GLACIER`, `DEEP_ARCHIVE`) via `CopyObject`, batch execution runner with `dry_run` preview, execution-time legal-hold race prevention, irreversible action safeguards, and real AWS storage pricing evaluation.

---

## 🚀 Quickstart Guide

### 1. Prerequisites
- Python 3.10+ (tested on Python 3.11 and Python 3.13)
- SQLite (built-in) or PostgreSQL 14+ (production)

### 2. Installation
```bash
git clone https://github.com/Ashoksundar18/Hospital_Data_Management.git
cd Hospital_Data_Management
pip install -r requirements.txt
```

### 3. Environment Variables
Create a `.env` file in the root directory:
```bash
# Database Configuration (Postgres for production, omitted for local SQLite)
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/hospital_test_db

# Role-Based Access Control (RBAC) Keys
API_KEY=admin-secret-key-12345
API_KEY_REVIEWER=reviewer-secret-key-67890
API_KEY_VIEWER=viewer-secret-key-abcde

# Cloud Credentials (AWS S3)
AWS_ACCESS_KEY_ID=your-aws-access-key
AWS_SECRET_ACCESS_KEY=your-aws-secret-key
AWS_DEFAULT_REGION=us-east-1

# Optional Development Flags
ALLOW_DEV_KEYS=0  # Set to 1 only in isolated local dev without env keys
```

### 4. Run Automated Test Suite
```bash
pytest -v
```
All 47 unit, governance, cost, and cloud execution tests pass hermetically using `moto[s3]`.

### 5. Launch Application Server
```bash
python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000 --reload
```
Navigate to:
- **Interactive UI Dashboard**: [http://localhost:8000](http://localhost:8000)
- **Interactive Swagger Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **Cryptographic Audit Chain**: [http://localhost:8000/api/v1/audit-log/verify](http://localhost:8000/api/v1/audit-log/verify)

---

## 📡 Complete REST API Reference

All requests must supply the authentication header: `X-API-Key: <key>`.

| Method | Path | Role | Description | Error Codes |
| :--- | :--- | :---: | :--- | :--- |
| `POST` | `/api/v1/objects` | `admin` | Ingests storage object metadata and generates recommendation | `400`, `401`, `403` |
| `GET` | `/api/v1/objects` | `viewer` | Lists all ingested storage objects | `401`, `403` |
| `GET` | `/api/v1/recommendations` | `viewer` | Lists all generated recommendations with status filters | `401`, `403` |
| `GET` | `/api/v1/recommendations/{id}` | `viewer` | Retrieves single recommendation by ID or Object ID | `401`, `403`, `404` |
| `POST` | `/api/v1/recommendations/{id}/confirm` | `reviewer` | Approves recommendation (`status -> confirmed`) | `400`, `401`, `403`, `404` |
| `POST` | `/api/v1/recommendations/{id}/override` | `reviewer` | Overrides recommendation with structured taxonomy | `400`, `401`, `403`, `404` |
| `POST` | `/api/v1/recommendations/{id}/periodic-review`| `reviewer` | Completes compliance review for legal-hold/retention locked objects | `400`, `401`, `403`, `404` |
| `POST` | `/api/v1/recommendations/{id}/rollback` | `admin` | Rolls back confirmed/overridden recommendation to pending | `400`, `401`, `403`, `404`, `409` |
| `POST` | `/api/v1/recommendations/{id}/execute` | `admin` | Executes confirmed recommendation on AWS S3 | `400`, `401`, `403`, `404`, `500` |
| `POST` | `/api/v1/execution/batch-run` | `admin` | Executes batch of confirmed recommendations with dry_run | `400`, `401`, `403` |
| `GET` | `/api/v1/cost/report` | `viewer` | Returns real AWS storage cost analysis before/after | `401`, `403` |
| `GET` | `/api/v1/audit-log` | `viewer` | Queries immutable audit log entries | `401`, `403` |
| `GET` | `/api/v1/audit-log/verify` | `viewer` | Cryptographically verifies SHA-256 audit chain | `401`, `403` |
| `GET` | `/health` | `public` | Application health and database connection probe | `503` (if DB down) |

### Key Endpoint Details

#### 1. Ingest Storage Object
```http
POST /api/v1/objects
X-API-Key: admin-secret-key-12345
Content-Type: application/json

{
  "id": "rad-mri-scan-2026-001",
  "bucket_or_account": "hospital-imaging-archive",
  "cloud_provider": "AWS",
  "data_classification": "DICOM",
  "current_storage_class": "HOT",
  "size_bytes": 524288000,
  "object_age_days": 180,
  "last_accessed_days": 95,
  "legal_hold": false
}
```

#### 2. Confirm Recommendation
```http
POST /api/v1/recommendations/rad-mri-scan-2026-001/confirm
X-API-Key: reviewer-secret-key-67890
Content-Type: application/json

{
  "justification": "Approved under radiological retention guideline Section 4."
}
```

#### 3. Execute Recommendation on Cloud Storage
```http
POST /api/v1/recommendations/rad-mri-scan-2026-001/execute
X-API-Key: admin-secret-key-12345
Content-Type: application/json

{
  "confirm_irreversible": false
}
```

#### 4. Batch Execution with Dry Run Preview
```http
POST /api/v1/execution/batch-run
X-API-Key: admin-secret-key-12345
Content-Type: application/json

{
  "bucket_or_account": "hospital-imaging-archive",
  "dry_run": true,
  "max_items": 100,
  "confirm_irreversible": false
}
```

---

## 🗄️ Database Schema & Entity Relationships

The relational model is maintained via SQLAlchemy ORM with automatic index generation:

```mermaid
erDiagram
    STORAGE_OBJECTS ||--o{ RECOMMENDATIONS : "generates"
    RECOMMENDATIONS ||--o{ CONFIRMATIONS_AND_OVERRIDES : "governed by"
    STORAGE_OBJECTS ||--o{ AUDIT_LOG : "tracked by"
    RECOMMENDATIONS ||--o{ AUDIT_LOG : "logged in"

    STORAGE_OBJECTS {
        string id PK
        string bucket_or_account
        string cloud_provider
        string data_classification
        string current_storage_class
        bigint size_bytes
        int object_age_days
        int last_accessed_days
        boolean legal_hold
        datetime created_at
    }

    RECOMMENDATIONS {
        string id PK
        string object_id FK
        string recommended_action
        string target_storage_class
        float estimated_cost_savings
        string confidence
        string evidence
        string approval_status
        boolean requires_periodic_review
        datetime last_reviewed_at
        datetime created_at
    }

    CONFIRMATIONS_AND_OVERRIDES {
        string id PK
        string recommendation_id FK
        string object_id FK
        string action_type
        string reviewer_id
        string override_reason
        text justification
        datetime timestamp
    }

    AUDIT_LOG {
        int id PK
        datetime timestamp
        string event_type
        string actor
        string object_id FK
        string recommendation_id FK
        text details_json
        string previous_hash
        string entry_hash
    }
```

---

## 🔒 Security, Safety Gates & Compliance

1. **Fail-Closed Authentication**: Unauthenticated requests are rejected with `HTTP 401`. Requests lacking appropriate privileges receive `HTTP 403`. Key identities are strictly mapped to `user_id` context to eliminate reviewer ID spoofing.
2. **Double-Checked Legal Hold Gate**: Legal hold status is validated at **Confirmation** and re-validated at **Execution**. Attempting to modify an object under legal hold halts execution immediately and writes `EXECUTION_BLOCKED_LEGAL_HOLD` to the audit log.
3. **Irreversible Action Safeguards**: Operations that permanently delete data or move objects to `DEEP_ARCHIVE` require explicit administrator authorization (`confirm_irreversible=True`) and documented justification.
4. **Advisory Transaction Locking**: On PostgreSQL backends, concurrent operations acquire `get_recommendation_with_lock` (row lock) and `pg_advisory_xact_lock(74294101)` to prevent race conditions or hash chain forks.

For clinical limitations, SLAs, and HIPAA deployment requirements, see [LIMITATIONS.md](LIMITATIONS.md).
For testing architecture and mocking details, see [TESTING.md](TESTING.md).

---

## 📄 License
This project is licensed under the Apache 2.0 License.
