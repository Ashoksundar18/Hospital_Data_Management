# Requirements Specification — Storage Lifecycle Recommender (Phase 1)

## 1. Context & Business Objectives
Hospital groups generate massive volumes of unstructured data across multi-cloud infrastructure (AWS, Azure, GCP):
- **Medical Images**: High-resolution DICOM files, radiology scans, pathology images.
- **Application Logs**: Audit trails, EHR application logs, server telemetry.
- **Backups**: Database snapshots, disaster recovery images, cold archives.

Currently, storage tiers (Hot, Cool, Cold, Archive, Deep Archive) are selected manually and old objects are rarely transitioned or deleted. This practice inflates multi-cloud operational costs and introduces compliance liabilities. 

The goal of the **Storage Lifecycle Recommender** is an automated, explainable, responsible-AI engine that recommends object-level storage class transitions or deletions while enforcing HIPAA/legal retention constraints and human oversight.

---

## 2. Functional Requirements (Phase 1)

### FR-1: Object & Retention Metadata Ingestion
- **Input Data Model**: The engine must ingest metadata for objects across cloud providers (`AWS`, `AZURE`, `GCP`).
- **Required Object Metadata**:
  - `id`: Unique identifier (string).
  - `bucket_or_account`: Storage bucket or account name (string).
  - `cloud_provider`: Provider type (`AWS`, `AZURE`, `GCP`).
  - `data_classification`: Object type (`MEDICAL_IMAGE`, `APP_LOG`, `BACKUP`).
  - `current_storage_class`: Current tier (`HOT`, `COOL`, `COLD`, `ARCHIVE`, `DEEP_ARCHIVE`).
  - `size_bytes`: Object file size in bytes (integer).
  - `object_age_days`: Object age since creation in days (integer).
  - `last_access_days_ago`: Days since last access (optional integer).
  - `access_frequency_30d`: Access count in past 30 days (optional integer).
  - `restore_event_history`: List of restore event records (optional list of dicts with `days_ago` integer).
  - `retention_rule_id`: Assigned retention rule ID (optional string).
  - `legal_hold`: Legal hold status flag (boolean).

### FR-2: Explainable Rule Engine Decision Factors & Catalog
The rules engine evaluates objects against deterministic rule-based criteria:

1. **`RULE_LEGAL_HOLD` (Hard Compliance Invariant)**:
   - If `legal_hold == True`, force `NO_ACTION`. Never recommend transition or deletion.
   - **Rule-Scoped Confidence**: Always `HIGH` (legal hold status is known metadata and does not depend on access/restore telemetry).

2. **`RULE_RETENTION_LOCK` (Anti-Destruction Deletion Lock)**:
   - If `object_age_days < min_retention_days` specified by the associated retention rule, `DELETE` recommendations are strictly forbidden.
   - **Scope Distinction**: `min_retention_days` models mandatory retention against object destruction (preventing premature deletion under regulatory mandates like HIPAA 7-year or HITECH 1-year). Retrieval latency SLAs are managed via classification transition paths and `RULE_RECENT_RESTORE`.
   - **Rule-Scoped Confidence**: Always `HIGH`.

3. **`RULE_EXPIRATION_DELETE` (Lifecycle Expiration)**:
   - If object age exceeds retention threshold (e.g. 365 days for logs, 1095 days for backups, 2555 days for medical images) AND `legal_hold == False` AND retention requirement is satisfied, recommend `DELETE`.
   - **Rule-Scoped Confidence**: Always `HIGH`.

4. **`RULE_RECENT_RESTORE` (Re-Access Thrashing Protection)**:
   - If an object was restored within the last 30 days (`restore_events.days_ago <= 30`), do not recommend moving to a colder storage class.
   - **Rule-Scoped Confidence**: `LOW` if restore telemetry is missing (`None`), `HIGH` if complete.

5. **`RULE_AGE_ACCESS_TRANSITION` (Tier Transition Optimization)**:
   - Evaluates step-down transitions (`HOT` ➔ `COOL` ➔ `COLD`/`ARCHIVE` ➔ `DEEP_ARCHIVE`) based on age and 30-day access frequency.
   - **Rule-Scoped Confidence**: `LOW` if access telemetry is missing (uses age fallback path).

6. **`RULE_NO_ACTION_DEFAULT` (Baseline Storage Alignment)**:
   - Issued when an object is not under legal hold, does not exceed maximum expiration lifecycle, has no recent restore events requiring protection override, and is already appropriately tiered for its age and access pattern.
   - **Rule-Scoped Confidence**: `HIGH` if telemetry complete; `LOW` if access or restore telemetry is missing.

### FR-3: Impact / Risk Tiering Precedence & Specification

Impact tier is evaluated as an explicit **if/elif evaluation chain in order of precedence**:

```
Step 1: Is action == NO_ACTION?
        ├── YES ➔ Return LOW_IMPACT (Zero infrastructure state change)
        └── NO  ➔ Proceed to Step 2

Step 2: Is action == DELETE?
        ├── YES ➔ Return HIGH_IMPACT (Permanent, irreversible data destruction)
        └── NO  ➔ Proceed to Step 3 (Action is TRANSITION)

Step 3: Does the TRANSITION meet any of these High-Risk criteria?
        ├── Target Class is ARCHIVE or DEEP_ARCHIVE (hours/days retrieval delay + early deletion fees)
        ├── Data Classification is MEDICAL_IMAGE (high clinical diagnostic criticality)
        ├── Object Size >= 50 GB (large transfer / operations risk)
        ├── YES ➔ Return HIGH_IMPACT
        └── NO  ➔ Return MEDIUM_IMPACT (Online tier transitions HOT->COOL, COOL->COLD for logs/backups)
```

> [!NOTE]
> **Action Risk vs. Data Sensitivity**: `ImpactTier` measures the operational risk of executing the recommended action (where `NO_ACTION` carries zero infrastructure state mutation risk), not the intrinsic legal sensitivity of the underlying data. In Phase 2, human-in-the-loop audit workflows may independently require periodic confirmation for high-stakes `NO_ACTION` cases (such as active legal holds or HIPAA retention locks) regardless of their `LOW_IMPACT` action rating.

#### Precedence Table:

| Precedence Order | Condition / Criteria | Resulting Impact Tier | Business Rationale |
|---|---|---|---|
| **1 (Highest)** | `recommended_action == NO_ACTION` | **`LOW_IMPACT`** | Zero state mutation on infrastructure. Overrides all metadata checks. |
| **2** | `recommended_action == DELETE` | **`HIGH_IMPACT`** | Permanent data destruction; highest regulatory risk. |
| **3a** | `target_storage_class in [ARCHIVE, DEEP_ARCHIVE]` | **`HIGH_IMPACT`** | Offline tier transition with multi-hour/day retrieval delays & fee commitments. |
| **3b** | `data_classification == MEDICAL_IMAGE` | **`HIGH_IMPACT`** | Active transition of clinical medical imaging files requires human review. |
| **3c** | `size_bytes >= 50 GB` | **`HIGH_IMPACT`** | Large storage object migration risk. |
| **4 (Default)** | Online transition (`HOT`➔`COOL`, `COOL`➔`COLD`) for `APP_LOG` / `BACKUP` | **`MEDIUM_IMPACT`** | Standard cost optimization transitions between online tiers. |

---

## 3. Non-Functional Requirements

### NFR-1: Rule-Scoped Graceful Degradation on Missing Data
- Missing telemetry feeds (`access_frequency_30d == None` or `restore_event_history == None`) must only degrade confidence for rules that actively consume telemetry.
- Hard compliance rules (`RULE_LEGAL_HOLD`, `RULE_RETENTION_LOCK`, `RULE_EXPIRATION_DELETE`) maintain `HIGH` confidence.
- Rules relying on telemetry attach `confidence_tier = "LOW"` and data completeness flags (`PARTIAL_MISSING_ACCESS`, `PARTIAL_MISSING_RESTORE`, or `INCOMPLETE`).

### NFR-2: Zero Compliance Breach (Safety Invariant)
- **Legal Hold Invariant**: Under zero circumstances may an object under `legal_hold == True` receive a `DELETE` or `TRANSITION` recommendation.
- **Retention Lock Invariant**: Under zero circumstances may an object whose age is less than `min_retention_days` receive a `DELETE` recommendation.

---

## 4. Scope Matrix

| Feature / Capability | Phase 1 (Foundations) | Phase 2 (Human-in-Loop & Explainability) | Phase 3 (Cost & Automation) |
|---|---|---|---|
| Core Rule-based Recommendation Engine | **In Scope** | Refinement | Refinement |
| Rule-Scoped Confidence & Precedence Impact Tiering | **In Scope** | Refinement | Refinement |
| Synthetic Dataset (275 objects, 6 double-missing) | **In Scope** | Benchmark Suite | Live Feed Ingestion |
| Minimal API Stub (`POST /objects`, `GET /recommendations`) | **In Scope** | Action Confirmation Endpoints | Cloud SDK Integration |
| Basic Prototype Dashboard UI | **In Scope** | Polish UI & Interactive Explanations | Full Analytics Portal |
| Unit Test Suite (10 Tests) | **In Scope** | Integration & E2E Tests | Stress & Performance Testing |
| Audit Trail / Change Log Database | Deferred | **In Scope** | Enterprise Audit Storage |
| Human Confirmation & Override Workflow | Deferred (Stubbed API) | **In Scope** | Multi-approver RBAC |
| Applied Action Rollback Engine | Deferred | Deferred | **In Scope** |
| Real-time Cost Savings Metrics Dashboard | Deferred | Deferred | **In Scope** |
