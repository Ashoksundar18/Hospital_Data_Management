# API Specification — Storage Lifecycle Recommender (Phase 2 Governance)

The Storage Lifecycle Recommender API exposes RESTful endpoints for object metadata ingestion, rule recommendation retrieval, human confirmation & taxonomy override workflows, cryptographic audit logging, and governance rollbacks.

Base URL: `http://localhost:8000/api/v1` (or live Render host URL)  
Interactive Swagger Documentation: `http://localhost:8000/docs`

> [!NOTE]
> **Database Persistence Layer**:
> All storage object metadata, retention rules, recommendation histories, approval decisions, and audit log entries are persisted durably in SQLite database `data/hospital_lifecycle.db`. State modifications survive server process restarts.

---

## Endpoints Summary

| Method | Endpoint | Description | Phase Status |
|---|---|---|---|
| `POST` | `/api/v1/objects` | Ingest/update storage object metadata & write audit entry | Implemented (SQLite) |
| `GET` | `/api/v1/objects` | List ingested storage objects | Implemented (SQLite) |
| `GET` | `/api/v1/recommendations` | Run rules engine & return recommendations | Implemented (SQLite) |
| `POST` | `/api/v1/recommendations/{id}/confirm` | Human approval confirmation | **Implemented (Phase 2)** |
| `POST` | `/api/v1/recommendations/{id}/override` | Human override with structured taxonomy reason | **Implemented (Phase 2)** |
| `POST` | `/api/v1/recommendations/{id}/periodic-review` | Compliance periodic review submission | **Implemented (Phase 2)** |
| `POST` | `/api/v1/recommendations/{id}/rollback` | Governance-level recommendation rollback | **Implemented (Phase 2)** |
| `GET` | `/api/v1/audit-log` | Retrieve hash-chained audit trail log entries | **Implemented (Phase 2)** |
| `GET` | `/api/v1/audit-log/verify` | Verify cryptographic hash chain integrity | **Implemented (Phase 2)** |

---

## Endpoint Details

### 1. Ingest Storage Object Metadata
- **Endpoint**: `POST /api/v1/objects`
- **Request Body**:
```json
{
  "id": "obj-img-10023",
  "bucket_or_account": "hospital-radiology-prod-aws",
  "cloud_provider": "AWS",
  "data_classification": "MEDICAL_IMAGE",
  "current_storage_class": "HOT",
  "size_bytes": 154200000,
  "object_age_days": 120,
  "last_access_days_ago": 45,
  "access_frequency_30d": 0,
  "restore_event_history": [],
  "retention_rule_id": "rule-hipaa-medical-image",
  "legal_hold": false
}
```
- **Response** (`201 Created`):
```json
{
  "status": "success",
  "message": "Storage object metadata ingested successfully",
  "object_id": "obj-img-10023"
}
```

---

### 2. Get Storage Lifecycle Recommendations
- **Endpoint**: `GET /api/v1/recommendations`
- **Query Parameters**:
  - `classification`: Filter by `MEDICAL_IMAGE`, `APP_LOG`, `BACKUP`.
  - `action`: Filter by `NO_ACTION`, `TRANSITION`, `DELETE`.
  - `approval_status`: Filter by `pending`, `confirmed`, `overridden`, `rolled_back`.
  - `missing_data_only`: Filter boolean.
- **Response** (`200 OK`):
```json
[
  {
    "id": "rec-03b8a38c",
    "object_id": "obj-img-10023",
    "current_class": "HOT",
    "recommended_action": "TRANSITION",
    "target_storage_class": "COOL",
    "triggering_rules": ["RULE_AGE_ACCESS_TRANSITION"],
    "evidence_snapshot": {
      "object_id": "obj-img-10023",
      "min_retrieval_tier": "COOL",
      "max_retrieval_latency_hours": 1
    },
    "confidence_tier": "HIGH",
    "impact_tier": "HIGH_IMPACT",
    "data_completeness_flag": "COMPLETE",
    "approval_status": "pending",
    "requires_periodic_review": false,
    "last_reviewed_at": null,
    "reasoning_summary": "Recommend transition from HOT to COOL: Object age (120d) >= 30 days.",
    "timestamp": "2026-09-18T10:00:00Z"
  }
]
```

---

### 3. Human Confirmation Approval
- **Endpoint**: `POST /api/v1/recommendations/{id}/confirm`
- **Request Body**:
```json
{
  "reviewer_id": "usr-compliance-admin"
}
```
- **Response** (`200 OK`): Returns updated `Recommendation` object with `approval_status: "confirmed"`. Writes `CONFIRM_RECOMMENDATION` audit entry.

---

### 4. Human Override with Structured Taxonomy
- **Endpoint**: `POST /api/v1/recommendations/{id}/override`
- **Taxonomy Options**: `PENDING_CLINICAL_TRIAL`, `LITIGATION_HOLD_EXTENDED`, `CUSTOM_SLA`, `DATA_QUALITY_CONCERN`, `OTHER`.
- **Request Body (Taxonomy Standard)**:
```json
{
  "reviewer_id": "usr-compliance-admin",
  "override_reason": "PENDING_CLINICAL_TRIAL"
}
```
- **Request Body (OTHER - Requires Text Detail)**:
```json
{
  "reviewer_id": "usr-compliance-admin",
  "override_reason": "OTHER",
  "other_reason_text": "Custom research data hold until Q4 audit"
}
```
- **Response** (`200 OK`): Returns updated `Recommendation` with `approval_status: "overridden"`.
- **Error Response** (`422 Unprocessable Entity`): Returned if `override_reason` is `OTHER` and `other_reason_text` is empty.

---

### 5. Governance Rollback
- **Endpoint**: `POST /api/v1/recommendations/{id}/rollback`
- **Request Body**:
```json
{
  "reviewer_id": "usr-audit-lead",
  "reason": "Reverting recommendation approval state due to revised audit policy."
}
```
- **Response** (`200 OK`): Returns updated `Recommendation` with `approval_status: "rolled_back"`. Writes `ROLLBACK_RECOMMENDATION` audit entry.

---

### 6. Audit Log Retrieval & Cryptographic Verification
- **Retrieval Endpoint**: `GET /api/v1/audit-log`
- **Verification Endpoint**: `GET /api/v1/audit-log/verify`
- **Verification Response** (`200 OK`):
```json
{
  "is_valid": true,
  "total_entries": 285,
  "tampered_entry_id": null,
  "message": "Audit chain integrity verified. All 285 entries are untampered and cryptographically valid."
}
```
