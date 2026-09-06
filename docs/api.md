# API Specification — Storage Lifecycle Recommender (Phase 1)

The Storage Lifecycle Recommender API exposes RESTful endpoints for object metadata ingestion, rule recommendation retrieval, and stubs for downstream human-approval workflows (Phase 2).

Base URL: `http://localhost:8000/api/v1` (or live Render host URL)  
Interactive Swagger Documentation: `http://localhost:8000/docs`

> [!NOTE]
> **In-Memory Data Preloading & State Persistence**:
> On application startup, the API automatically preloads regulatory retention rules from `data/retention_rules.json` and 275 synthetic storage objects from `data/synthetic_objects.json` into an in-memory database (`OBJECTS_DB`). State modifications (e.g. via `POST /api/v1/objects`) exist in memory during the process lifetime and are **not persistent across server restarts**. Persistent audit logging and database storage are explicitly planned for Phase 2.

---

## Endpoints

### 1. Ingest Storage Object Metadata
- **Endpoint**: `POST /api/v1/objects`
- **Description**: Registers or updates a storage object's metadata in the system.
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
- **Error Response** (`422 Unprocessable Entity`):
```json
{
  "detail": [
    {
      "loc": ["body", "object_age_days"],
      "msg": "Input should be greater than or equal to 0",
      "type": "greater_than_equal"
    }
  ]
}
```

---

### 2. List All Ingested Storage Objects
- **Endpoint**: `GET /api/v1/objects`
- **Description**: Returns all currently ingested storage objects.
- **Query Parameters**:
  - `classification` (optional): Filter by `MEDICAL_IMAGE`, `APP_LOG`, or `BACKUP`.
  - `legal_hold` (optional boolean): Filter by legal hold status.
- **Response** (`200 OK`):
```json
[
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
]
```

---

### 3. Get Storage Lifecycle Recommendations
- **Endpoint**: `GET /api/v1/recommendations`
- **Description**: Runs the deterministic rules engine against all ingested objects and returns calculated recommendations with triggering rules and evidence snapshots.
- **Query Parameters**:
  - `classification` (optional): Filter by `MEDICAL_IMAGE`, `APP_LOG`, or `BACKUP`.
  - `action` (optional): Filter by `NO_ACTION`, `TRANSITION`, or `DELETE`.
  - `missing_data_only` (optional boolean): Filter for objects with `LOW` confidence due to missing access or restore telemetry.
- **Response** (`200 OK`):
```json
[
  {
    "object_id": "obj-img-10023",
    "current_class": "HOT",
    "recommended_action": "TRANSITION",
    "target_storage_class": "COOL",
    "triggering_rules": ["RULE_AGE_ACCESS_TRANSITION"],
    "evidence_snapshot": {
      "object_id": "obj-img-10023",
      "cloud_provider": "AWS",
      "data_classification": "MEDICAL_IMAGE",
      "current_storage_class": "HOT",
      "object_age_days": 120,
      "last_access_days_ago": 45,
      "access_frequency_30d": 0,
      "has_recent_restore_30d": false,
      "legal_hold": false,
      "retention_rule_id": "rule-hipaa-medical-image",
      "min_retention_days": 2555,
      "retention_satisfied": false,
      "missing_access_data": false,
      "missing_restore_data": false
    },
    "confidence_tier": "HIGH",
    "impact_tier": "HIGH_IMPACT",
    "data_completeness_flag": "COMPLETE",
    "reasoning_summary": "Recommend transition from HOT to COOL: Object age (120d) >= 30 days and access frequency in past 30 days is 0.",
    "timestamp": "2026-09-06T19:00:00Z"
  }
]
```

---

### 4. Stubbed Endpoint: Confirm Recommendation (Phase 2 Stub)
- **Endpoint**: `POST /api/v1/recommendations/{object_id}/confirm`
- **Description**: Placeholder endpoint for Phase 2 human-approval flow to confirm a recommendation.
- **Response** (`501 Not Implemented`):
```json
{
  "status": "deferred",
  "message": "Human confirmation approval workflow is deferred to Phase 2."
}
```
- **Error Response** (`404 Not Found`):
```json
{
  "detail": "Storage object 'obj-unknown' not found"
}
```

---

### 5. Stubbed Endpoint: Override Recommendation (Phase 2 Stub)
- **Endpoint**: `POST /api/v1/recommendations/{object_id}/override`
- **Description**: Placeholder endpoint for Phase 2 human override with rationale logging.
- **Response** (`501 Not Implemented`):
```json
{
  "status": "deferred",
  "message": "Human override workflow and audit logging are deferred to Phase 2."
}
```
- **Error Response** (`404 Not Found`):
```json
{
  "detail": "Storage object 'obj-unknown' not found"
}
```
