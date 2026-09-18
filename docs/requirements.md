# Requirements Specification — Storage Lifecycle Recommender (Phase 2 Governance)

## 1. Context & Operational Governance Objectives
Hospital groups generate massive volumes of unstructured data across multi-cloud infrastructure (AWS, Azure, GCP):
- **Medical Images**: High-resolution DICOM files, radiology scans, pathology images.
- **Application Logs**: Audit trails, EHR application logs, server telemetry.
- **Backups**: Database snapshots, disaster recovery images, cold archives.

Phase 1 established a deterministic rule-based storage-lifecycle recommendation engine.  
**Phase 2** builds the operational governance layer wrapping the engine:
1. **Persistent Storage**: Migrates object metadata and recommendation histories from ephemeral in-memory storage to a durable SQLite database (`data/hospital_lifecycle.db` via SQLAlchemy ORM).
2. **Retrieval-SLA Enforcement**: Formally models retrieval latency expectations (`min_retrieval_tier`, `max_retrieval_latency_hours`) in `RetentionRule` and introduces `RULE_RETRIEVAL_SLA`.
3. **Human-in-the-Loop Approval Workflows**: Requires explicit human reviewer approval (`confirm`) or structured taxonomy overrides (`override`) for `HIGH_IMPACT` recommendations before execution.
4. **Compliance Periodic Review**: Flags compliance-sensitive `NO_ACTION` outcomes (`RULE_LEGAL_HOLD`, `RULE_RETENTION_LOCK`) with `requires_periodic_review: true` for periodic re-confirmation.
5. **Cryptographic Tamper-Evident Audit Trail**: Appends immutable SHA-256 hash-chained audit log entries for all state mutations with a chain verification engine (`GET /api/v1/audit-log/verify`).
6. **Governance-Level Rollback Path**: Records recommendation status rollbacks (`rolled_back`) with audit trail tracking.

---

## 2. Functional Requirements (Phase 2)

### FR-1: Database Persistence Layer
- All `StorageObject` metadata, `RetentionRule` definitions, `Recommendation` histories, `ConfirmationOverride` records, and `AuditLog` entries are stored durably in SQLite database `data/hospital_lifecycle.db`.
- Data and recommendation histories survive process restarts and server deployments.

### FR-2: Explainable Rule Engine Catalog (7 Rules)
The rules engine evaluates objects against deterministic rule-based criteria:

1. **`RULE_LEGAL_HOLD` (Hard Compliance Invariant)**:
   - If `legal_hold == True`, force `NO_ACTION`. Never recommend transition or deletion.
   - **Rule-Scoped Confidence**: Always `HIGH`.
   - **Governance Flag**: Sets `requires_periodic_review = True`.

2. **`RULE_RETENTION_LOCK` (Anti-Destruction Deletion Lock)**:
   - If `object_age_days < min_retention_days` specified by the associated retention rule, `DELETE` recommendations are strictly forbidden.
   - **Rule-Scoped Confidence**: Always `HIGH`.
   - **Governance Flag**: Sets `requires_periodic_review = True` for `NO_ACTION` outcomes.

3. **`RULE_RETRIEVAL_SLA` (Retrieval Latency SLA Enforcement - Phase 2)**:
   - If an applicable `RetentionRule` specifies `min_retrieval_tier` (e.g. `COOL` or `COLD`), candidate transitions to colder storage classes (e.g. `ARCHIVE` or `DEEP_ARCHIVE`) are downgraded to `min_retrieval_tier` or blocked.
   - **Fallback**: If `min_retrieval_tier` is `None`, the engine executes standard Option-A deletion-blocking logic.
   - **Rule-Scoped Confidence**: Always `HIGH`.

4. **`RULE_EXPIRATION_DELETE` (Lifecycle Expiration)**:
   - If object age exceeds retention threshold (365d logs, 1095d backups, 2555d images) AND `legal_hold == False` AND retention requirement is satisfied, recommend `DELETE`.
   - **Rule-Scoped Confidence**: Always `HIGH`.

5. **`RULE_RECENT_RESTORE` (Re-Access Thrashing Protection)**:
   - If an object was restored within the last 30 days (`restore_events.days_ago <= 30`), do not recommend moving to a colder storage class.
   - **Rule-Scoped Confidence**: `LOW` if restore telemetry is missing (`None`), `HIGH` if complete.

6. **`RULE_AGE_ACCESS_TRANSITION` (Tier Transition Optimization)**:
   - Evaluates step-down transitions (`HOT` ➔ `COOL` ➔ `COLD`/`ARCHIVE` ➔ `DEEP_ARCHIVE`) based on age and 30-day access frequency.
   - **Rule-Scoped Confidence**: `LOW` if access telemetry is missing (uses age fallback path).

7. **`RULE_NO_ACTION_DEFAULT` (Baseline Storage Alignment)**:
   - Issued when an object is not under legal hold, does not exceed maximum expiration lifecycle, has no recent restore events requiring protection override, and is already appropriately tiered for its age and access pattern.
   - **Rule-Scoped Confidence**: `HIGH` if telemetry complete; `LOW` if access or restore telemetry is missing.

### FR-3: Impact / Risk Tiering Precedence Specification

Impact tier is evaluated as an explicit **if/elif evaluation chain in order of precedence**:

1. **Precedence 1**: `recommended_action == NO_ACTION` ➔ `LOW_IMPACT` (Zero infrastructure state change).
2. **Precedence 2**: `recommended_action == DELETE` ➔ `HIGH_IMPACT` (Permanent data destruction).
3. **Precedence 3**: `recommended_action == TRANSITION` AND meets any:
   - Target class is `ARCHIVE` or `DEEP_ARCHIVE`.
   - Data classification is `MEDICAL_IMAGE`.
   - Object size `size_bytes >= 50 GB`.
   ➔ `HIGH_IMPACT`
4. **Precedence 4 (Default)**: Online transitions (`HOT`➔`COOL`, `COOL`➔`COLD`) for `APP_LOG`/`BACKUP` ➔ `MEDIUM_IMPACT`.

> [!NOTE]
> **Action Risk vs. Data Sensitivity**: `ImpactTier` measures the operational risk of executing the recommended action (where `NO_ACTION` carries zero infrastructure state mutation risk), not the intrinsic legal sensitivity of the underlying data. In Phase 2, compliance-sensitive `NO_ACTION` items set `requires_periodic_review = True` for periodic re-confirmation regardless of their `LOW_IMPACT` action rating.

### FR-4: Human-in-the-Loop Confirmation & Override Taxonomy
- **Confirmation (`POST /confirm`)**: Requires `reviewer_id`. Sets `approval_status = "confirmed"`.
- **Override (`POST /override`)**: Requires `reviewer_id` and a structured `override_reason` taxonomy:
  - `PENDING_CLINICAL_TRIAL`
  - `LITIGATION_HOLD_EXTENDED`
  - `CUSTOM_SLA`
  - `DATA_QUALITY_CONCERN`
  - `OTHER` (Strictly requires non-empty `other_reason_text` detail).
- Sets `approval_status = "overridden"`.
- **Governance Rollback (`POST /rollback`)**: Requires `reviewer_id` and `reason`. Sets `approval_status = "rolled_back"`.

---

## 3. Cryptographic Tamper-Evident Audit Trail Specification

### Hash Chaining Mechanics
Every state-changing API call (ingestion, recommendation generation, confirmation, override, periodic review, rollback) writes an immutable entry to `audit_log`:
$$\text{entry\_hash} = \text{SHA256}(\text{event\_type} \mid \text{actor} \mid \text{object\_id} \mid \text{recommendation\_id} \mid \text{details\_json} \mid \text{previous\_hash})$$

- **Genesis Entry**: The first audit entry links to `previous_hash = "0000000000000000000000000000000000000000000000000000000000000000"`.
- **Tamper Detection**: `GET /api/v1/audit-log/verify` sequentially recalculates SHA-256 signatures across all log entries. If any historical database row is modified or deleted, the hash chain breaks and identifies the exact tampered entry ID.

---

## 4. Scope Matrix

| Feature / Capability | Phase 1 (Foundations) | Phase 2 (Governance & Human-in-Loop) | Phase 3 (Cost & Automation) |
|---|---|---|---|
| Core Rule-based Recommendation Engine | **In Scope** | Refinement | Refinement |
| SQLite Database Persistence Layer | Ephemeral In-Memory | **In Scope (SQLite)** | PostgreSQL Migration (if needed) |
| Retrieval-SLA Rule Extension (`RULE_RETRIEVAL_SLA`) | Documented Limitation | **In Scope** | Cloud SLA Verification |
| Human Confirmation & Override Taxonomy | Deferred (501 Stubs) | **In Scope** | Multi-approver RBAC |
| Cryptographic Hash-Chained Audit Trail | Deferred | **In Scope** | Enterprise Audit Export |
| Governance-Level Rollback Engine | Deferred | **In Scope (Governance Record)** | Live Cloud State Reversal |
| Interactive Dashboard UI (Approvals & Audit Log) | Prototype Display | **In Scope** | Full Analytics Portal |
| Applied Action Real Cloud Provider Mutation | Deferred | Deferred | **In Scope** |
| Real-time Cost Savings Metrics Dashboard | Deferred | Deferred | **In Scope** |
