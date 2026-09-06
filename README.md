# Storage Lifecycle Recommender — Phase 1 (Foundations)

A responsible-AI storage lifecycle recommender prototype built for hospital groups managing medical images, application logs, and database backups across multi-cloud environments (AWS, Azure, GCP).

---

## 📋 Prerequisites & Python Environment

- **Python Version**: Python 3.10 or higher (tested on **Python 3.13.12**).
- **Operating System**: macOS, Linux, or Windows.

---

## 🚀 Copy-Pasteable Setup & Run Instructions (Fresh Clone)

Follow these step-by-step instructions on a fresh clone of the repository:

### 1. Clone the Repository
```bash
git clone https://github.com/Ashoksundar18/Hospital_Data_Management.git
cd Hospital_Data_Management
```

### 2. Create & Activate Virtual Environment

**On macOS / Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

**On Windows (PowerShell):**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

**On Windows (Command Prompt):**
```cmd
python -m venv .venv
.venv\Scripts\activate.bat
```

### 3. Install Pinned Dependencies
```bash
pip install -r requirements.txt
```

### 4. Run the Test Suite
Execute the Pytest suite to verify that all compliance invariants (Legal Hold, Retention Lock, Rule-Scoped Confidence, Impact Tiering) pass:
```bash
pytest tests/ -v
```

### 5. Launch the API Server & Dashboard UI
```bash
python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000 --reload
```

### 6. Access Application Dashboards & OpenAPI Docs
Once the server is running, open your browser:
- **Interactive UI Dashboard**: [http://localhost:8000](http://localhost:8000)
- **Interactive Swagger API Docs (OpenAPI)**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **Raw Recommendations JSON**: [http://localhost:8000/api/v1/recommendations](http://localhost:8000/api/v1/recommendations)

> [!NOTE]
> **In-Memory Data Preloading & Persistence Notice**:  
> On startup, the server automatically preloads 275 synthetic storage objects from `data/synthetic_objects.json` and compliance rules from `data/retention_rules.json`. In-memory modifications (e.g. via `POST /api/v1/objects`) exist during process lifetime and are reset on server restart. Persistent audit trail database storage is planned for Phase 2.

---

## 🧠 Core Rules Engine Catalog & Confidence Architecture

The rules engine (`src/engine/rules_engine.py`) operates deterministically with **Rule-Scoped Confidence** (confidence reflects the certainty of the specific rule that fired, rather than blanket-lowering confidence when unrelated telemetry is missing):

1. **`RULE_LEGAL_HOLD` (Hard Compliance Invariant)**:
   - If `legal_hold == True`, engine unconditionally returns `NO_ACTION`.
   - **Confidence**: Always `HIGH` (legal hold status is known metadata and does not depend on access/restore telemetry).

2. **`RULE_RETENTION_LOCK` (Anti-Destruction Deletion Lock)**:
   - If `object_age_days < min_retention_days`, deletion is strictly prohibited even if age exceeds max policy or access frequency is zero.
   - **Retention vs. Retrieval SLA Scope**: `min_retention_days` models anti-destruction retention rules. Retrieval latency SLAs are managed via data classification transition paths and `RULE_RECENT_RESTORE`.
   - **Confidence**: Always `HIGH`.

3. **`RULE_EXPIRATION_DELETE`**:
   - Recommends deletion if object age exceeds maximum lifecycle policy (7 years for images, 1 year for logs, 3 years for backups) AND retention requirement is satisfied AND legal hold is false.
   - **Confidence**: Always `HIGH`.

4. **`RULE_RECENT_RESTORE`**:
   - If an object was restored within the past 30 days (`restore_events.days_ago <= 30`), transition to a colder tier is deferred to prevent performance thrashing.
   - **Confidence**: `LOW` if restore telemetry is missing (`None`), `HIGH` if complete.

5. **`RULE_AGE_ACCESS_TRANSITION`**:
   - Evaluates step-down transitions (`HOT` ➔ `COOL` ➔ `COLD`/`ARCHIVE` ➔ `DEEP_ARCHIVE`) based on object age and 30-day access frequency.
   - **Confidence**: `LOW` if access telemetry is missing (uses conservative age fallback path).

6. **`RULE_NO_ACTION_DEFAULT` (Baseline Storage Alignment)**:
   - Triggered when an object is not under legal hold, does not exceed maximum expiration lifecycle, has no recent restore events requiring protection override, and is already appropriately tiered for its age.
   - **Confidence**: `HIGH` if telemetry complete; `LOW` if access or restore telemetry is missing.

---

## 🎯 Impact / Risk Tiering Precedence & Criteria

Impact tier is calculated via an explicit **if/elif precedence chain**:

1. **`LOW_IMPACT` (Precedence 1)**: Action is `NO_ACTION`. Zero state mutation on infrastructure. Overrides all classification/size checks.
2. **`HIGH_IMPACT` (Precedence 2)**: Action is `DELETE` (irreversible loss risk).
3. **`HIGH_IMPACT` (Precedence 3)**: Action is `TRANSITION` AND meets any of:
   - Target class is `ARCHIVE` or `DEEP_ARCHIVE` (long retrieval latency of hours/days, early deletion fees).
   - Data classification is `MEDICAL_IMAGE` (high clinical diagnostic criticality).
   - Object size `size_bytes >= 50 GB` (large transfer / operations risk).
4. **`MEDIUM_IMPACT` (Precedence 4 - Default)**: Online tier transitions (`HOT` ➔ `COOL`, `COOL` ➔ `COLD`) for `APP_LOG` or `BACKUP`.

*Note on Action Risk vs. Data Sensitivity*: `ImpactTier` measures the operational risk of executing the recommended action (where `NO_ACTION` carries zero infrastructure state mutation risk), not the intrinsic legal sensitivity of the underlying data. In Phase 2, human-in-the-loop audit workflows may independently require periodic confirmation for high-stakes `NO_ACTION` cases (such as active legal holds or HIPAA retention locks) regardless of their `LOW_IMPACT` action rating.

---

## 📊 Missing Data & Overlap Statistics

Dataset (`data/synthetic_objects.json`, 275 total items):
- **Missing Access Telemetry (`access_frequency_30d == null`)**: 41 objects (14.9%)
- **Missing Restore Telemetry (`restore_event_history == null`)**: 28 objects (10.2%)
- **Double-Missing Subsets (Both Access & Restore Telemetry Missing)**: 6 objects (2.2%) — exercises the most conservative fallback path (`DataCompletenessFlag.INCOMPLETE`).

---

## 🌐 Render Cloud Deployment Prep

This project is pre-configured for 1-click persistent deployment on [Render](https://render.com).

### Render Configuration Files Included:
- **`render.yaml`**: Render Blueprint specification.
- **`Procfile`**: Defines start command `web: uvicorn src.api.main:app --host 0.0.0.0 --port $PORT`.

### Deployment Steps on Render:
1. Log in to [Render Dashboard](https://dashboard.render.com).
2. Click **New +** ➔ **Web Service** (or **Blueprint**).
3. Connect your GitHub repository `https://github.com/Ashoksundar18/Hospital_Data_Management.git`.
4. Configure service settings:
   - **Environment**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn src.api.main:app --host 0.0.0.0 --port $PORT`
5. Click **Deploy Web Service**. Once deployed, Render provides a public HTTPS live demo URL.

---

## 🧪 Acceptance Criteria Status

- [x] Requirements doc exists and covers scope, factors, rule catalog, and constraints (`docs/requirements.md`).
- [x] Data model is implemented with Pydantic v2 validation (`src/models/`).
- [x] Rules engine runs end-to-end on synthetic dataset with attached rule/evidence (`src/engine/rules_engine.py`).
- [x] Rule-Scoped confidence fix verified: Legal hold recommendations are ALWAYS `HIGH` confidence regardless of missing telemetry (`tests/test_rules_engine.py`).
- [x] Precedence-based Impact Tiering defined and documented (`LOW_IMPACT` for `NO_ACTION` first, then `HIGH_IMPACT` for `DELETE` / `MEDICAL_IMAGE` / `ARCHIVE`).
- [x] Double-missing data subset verified (6 objects missing both telemetry feeds).
- [x] Engine refuses to recommend deletion for legal-hold or retention-not-satisfied objects.
- [x] API stub is callable and returns recommendations as structured JSON (`GET /api/v1/recommendations`).
- [x] A basic UI screen displays recommendations with visible reasoning (`http://localhost:8000`).
- [x] README lets a new person run the whole project in under 5 minutes.
