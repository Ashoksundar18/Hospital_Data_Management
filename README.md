# Storage Lifecycle Recommender — Phase 2 (Governance & Human-in-the-Loop)

A responsible-AI storage lifecycle recommender prototype built for hospital groups managing medical images, application logs, and database backups across multi-cloud environments (AWS, Azure, GCP).

> **Deployment Status**: Live on Render with Hosted PostgreSQL Persistence & Hash-Chained Audit Logging.

---

## 🚀 Quickstart Guide (< 5 Minutes)

### 1. Prerequisites
- Python 3.10+ (tested on **Python 3.13.12**).

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Run Test Suite
Verify that all compliance invariants, retrieval SLA rules, human workflows, and cryptographic tamper-detection tests pass:
```bash
pytest tests/ -v
```

### 4. Launch API & Prototype Web Dashboard
```bash
python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000 --reload
```
Open your browser and navigate to:
- **Interactive UI Dashboard & Audit Log**: [http://localhost:8000](http://localhost:8000)
- **Swagger REST API Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **Cryptographic Audit Chain Verification**: [http://localhost:8000/api/v1/audit-log/verify](http://localhost:8000/api/v1/audit-log/verify)

---

## 🧠 Rules Engine Catalog (7 Rules)

The rules engine (`src/engine/rules_engine.py`) operates deterministically with **Rule-Scoped Confidence**:

1. **`RULE_LEGAL_HOLD` (Hard Compliance Invariant)**:
   - If `legal_hold == True`, forces `NO_ACTION`.
   - **Confidence**: Always `HIGH`. Flags `requires_periodic_review = True`.
2. **`RULE_RETENTION_LOCK` (Anti-Destruction Deletion Lock)**:
   - If `object_age_days < min_retention_days`, deletion is strictly prohibited.
   - **Confidence**: Always `HIGH`. Flags `requires_periodic_review = True`.
3. **`RULE_RETRIEVAL_SLA` (Phase 2 Retrieval SLA)**:
   - If an applicable `RetentionRule` specifies `min_retrieval_tier` (e.g. `COOL` or `COLD`), candidate transitions to colder tiers are downgraded or blocked.
   - **Confidence**: Always `HIGH`.
4. **`RULE_EXPIRATION_DELETE`**:
   - Recommends deletion if age exceeds maximum lifecycle policy AND retention is satisfied AND legal hold is false.
   - **Confidence**: Always `HIGH`.
5. **`RULE_RECENT_RESTORE`**:
   - Defers colder transitions if object was restored within past 30 days.
   - **Confidence**: `LOW` if restore telemetry is missing, `HIGH` if complete.
6. **`RULE_AGE_ACCESS_TRANSITION`**:
   - Evaluates step-down transitions (`HOT` ➔ `COOL` ➔ `COLD`/`ARCHIVE` ➔ `DEEP_ARCHIVE`) based on age and 30-day access frequency.
   - **Confidence**: `LOW` if access telemetry is missing.
7. **`RULE_NO_ACTION_DEFAULT`**:
   - Issued when an object is appropriately tiered.
   - **Confidence**: `HIGH` if telemetry complete; `LOW` if access/restore telemetry missing.

---

## 🎯 Human Governance & Cryptographic Audit Trail

### 1. SQLite Database Persistence
- All object metadata, retention rules, recommendations, human approval decisions, and audit entries are persisted in SQLite database `data/hospital_lifecycle.db` via SQLAlchemy ORM.
- Recommendation history and object state survive server restarts.

### 2. Human Approval & Override Taxonomy
- `POST /api/v1/recommendations/{id}/confirm`: Approves recommendation (`approval_status = "confirmed"`).
- `POST /api/v1/recommendations/{id}/override`: Rejects/modifies recommendation with structured taxonomy: `PENDING_CLINICAL_TRIAL`, `LITIGATION_HOLD_EXTENDED`, `CUSTOM_SLA`, `DATA_QUALITY_CONCERN`, `OTHER` (enforces mandatory text for `OTHER`).

### 3. Cryptographic Hash-Chained Audit Trail
Every state mutation appends an immutable entry to `audit_log`:
$$\text{entry\_hash} = \text{SHA256}(\text{event\_type} \mid \text{actor} \mid \text{object\_id} \mid \text{recommendation\_id} \mid \text{details\_json} \mid \text{previous\_hash})$$
- Verification via `GET /api/v1/audit-log/verify` re-computes SHA-256 signatures and detects any database-level tampering.

---

## 🧪 Acceptance Criteria Status (16/16 Passed)

- [x] Object and recommendation state persist across server restarts (`test_db_persistence_across_restart`).
- [x] Retrieval SLA constraints enforced via `RULE_RETRIEVAL_SLA` with Option-A fallback (`test_retrieval_sla_rule_enforcement`).
- [x] Confirm and override endpoints fully functional with taxonomy validation (`test_confirm_and_override_workflows`).
- [x] Distinct compliance periodic review flag & workflow implemented (`test_compliance_periodic_review`).
- [x] Cryptographic hash-chained audit log with DB tamper detection (`test_hash_chained_audit_log_and_tamper_detection`).
- [x] Governance-level recommendation rollback workflow implemented (`test_governance_rollback_workflow`).
- [x] Dashboard updated with human action controls and live Audit Log viewer.
- [x] All 16 unit tests passing (100% pass rate).
