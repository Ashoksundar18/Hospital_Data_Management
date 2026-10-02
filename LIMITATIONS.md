# System Limitations & Compliance Governance Boundary

This document outlines the architectural boundaries, provider limitations, and compliance safeguards of the **Hospital Storage Lifecycle Recommender (Phases 1–3)**. It is intended for Hospital IT Directors, Information Security Officers (CISO), and HIPAA Compliance Stakeholders.

---

## 1. Architectural & Provider Scope Boundaries

### 1. Provider Target (AWS S3)
- **Current Support**: Automated cloud execution (`S3StorageExecutor`) is fully implemented for **AWS S3** storage tiers (`STANDARD`, `STANDARD_IA`, `GLACIER`, `DEEP_ARCHIVE`).
- **Multi-Cloud Status**: Azure Blob Storage (Hot, Cool, Cold, Archive) and Google Cloud Storage (Standard, Nearline, Coldline, Archive) are supported in the **deterministic rules engine** and recommendation metadata, but physical cloud transition execution currently requires AWS S3 credentials.

### 2. Single-Region & Cross-Region Replication (CRR)
- The executor performs in-place transitions within the source bucket/region using `CopyObject`.
- It does **not** automatically update AWS S3 Cross-Region Replication (CRR) rules. If the target bucket replicates to a replica bucket in another region, hospital IT must configure lifecycle policies or replication tier parity on the replica bucket independently.

### 3. No Inter-Cloud Arbitrage / Egress Migration
- The system does not attempt live data migration across clouds (e.g. AWS S3 to Azure Blob). Egress bandwidth costs across cloud boundaries ($0.09/GB) would rapidly exceed any tiered storage cost savings.

### 4. S3 Request Rate Limits & Throttling
- Amazon S3 supports up to 3,500 `PUT`/`COPY`/`POST`/`DELETE` requests per second per prefix.
- The built-in batch runner (`POST /api/v1/execution/batch-run`) executes items synchronously in bounded batches (default 100, max 1,000 items) to prevent API rate exhaustion. For multi-million object inventories, scheduled execution windows or AWS S3 Batch Operations should be scheduled in stages.

---

## 2. Clinical SLAs & Storage Tier Constraints

### 1. Retrieval Latency & Emergency Care
- **GLACIER**: Standard retrieval requires **3 to 5 hours**. Expedited retrieval requires 1 to 5 minutes at significant surcharge.
- **DEEP_ARCHIVE**: Retrieval requires **12 hours** (Standard) or up to 48 hours (Bulk).
- **Clinical Directive**: Objects subject to active clinical trials, trauma center recall, or emergency radiology review must **never** be placed in `GLACIER` or `DEEP_ARCHIVE`. The rules engine enforces this via `RULE_RETRIEVAL_SLA` (`min_retrieval_tier="COOL"` or `"COLD"`).

### 2. Minimum Storage Duration Surcharges
- Cloud providers enforce minimum billing retention windows:
  - AWS `STANDARD_IA`: 30-day minimum charge.
  - AWS `GLACIER`: 90-day minimum charge.
  - AWS `DEEP_ARCHIVE`: 180-day minimum charge.
- Deleting or transitioning an object before its minimum billing window results in early deletion prorated charges.

### 3. Point-in-Time Cost Estimates vs. Live AWS Billing
- **Static Baseline**: Cost figures computed by `/api/v1/cost/report` use hardcoded published AWS S3 US-East-1 pricing ($0.023 Standard, $0.0125 Standard-IA, $0.004 Glacier Instant Retrieval, $0.0036 Glacier Flexible, $0.00099 Glacier Deep Archive per GB-month).
- **Not Live Pricing API**: The system does not dynamically query the AWS Price List API. Real healthcare cloud bills vary based on:
  - Region-specific rate variances (e.g., AWS GovCloud or European Union regions).
  - Enterprise Discount Programs (EDP) negotiated by health systems.
  - Per-request API transaction costs (e.g. $0.005 per 1,000 CopyObject requests) and data retrieval fees.
- **Stakeholder Guidance**: All projected savings must be interpreted by hospital financial officers as architectural baseline estimates rather than binding invoices.

---

## 3. Human-in-the-Loop & Decision Support Model

- **Advisory Prototype**: The recommender is designed as an **augmented intelligence / decision support tool**, not an unsupervised autonomous deletion bot.
- **Irreversible Confirmation**: Actions classified as irreversible (`DELETE` or transition to `DEEP_ARCHIVE`) cannot be executed by the batch runner without explicit administrator authorization (`confirm_irreversible=True`) and documented business justification.

---

## 4. Hospital IT & HIPAA Compliance Checklist

Before enabling automated cloud execution in a production clinical environment, ensure the following controls are configured:

| Control Area | Requirement | Verification Method | Status |
| :--- | :--- | :--- | :---: |
| **Data Protection** | S3 Server-Side Encryption with KMS (SSE-KMS) enabled on all buckets | AWS Console / IAM Policy | [ ] |
| **Immutability (WORM)** | S3 Object Lock in Compliance Mode enabled for HIPAA medical records (EHR / DICOM) | Bucket Versioning + Object Lock | [ ] |
| **Credential Security** | Least-privilege IAM policy scoped strictly to `s3:CopyObject`, `s3:GetObject`, `s3:DeleteObject` on designated buckets | AWS IAM Role / Render Secrets | [ ] |
| **No Hardcoded Keys** | API keys and AWS credentials passed strictly via environment variables | Codebase Audit & CI check | [x] |
| **Audit Verification** | Daily verification of SHA-256 audit log integrity via `/api/v1/audit-log/verify` | Cron monitoring script | [x] |
| **Legal Hold Sync** | Hospital legal hold registry synchronized with `/api/v1/objects` before batch runs | Ingestion pipeline | [ ] |
| **Reviewer Authentication** | Enforce distinct API keys for Reviewer and Admin roles with Anti-Spoofing enabled | `src/api/auth.py` | [x] |

### Least-Privilege IAM Policy Template
Hospital AWS administrators must scope credentials strictly to designated archive buckets rather than granting wildcard `s3:*` permissions:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowHospitalLifecycleTransitions",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:GetObjectVersion",
        "s3:PutObject",
        "s3:PutObjectAcl",
        "s3:GetObjectAcl",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::hospital-imaging-archive-*",
        "arn:aws:s3:::hospital-imaging-archive-*/*"
      ]
    },
    {
      "Sid": "AllowRestrictedDeletions",
      "Effect": "Allow",
      "Action": [
        "s3:DeleteObject"
      ],
      "Resource": [
        "arn:aws:s3:::hospital-logs-*/*"
      ]
    }
  ]
}
```

---

## 5. Summary

The Hospital Storage Lifecycle Recommender provides mathematical and cryptographic guarantees of policy consistency and audit immutability. When combined with cloud provider WORM storage (S3 Object Lock) and strict IAM least-privilege scoping, it eliminates the risks of premature clinical data deletion while achieving predictable cloud storage cost optimization.
