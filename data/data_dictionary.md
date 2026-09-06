# Data Dictionary — Storage Lifecycle Recommender

This document defines the schema, field descriptions, data types, and permitted value ranges for `data/synthetic_objects.json` and system data models.

---

## 1. StorageObject Schema (`data/synthetic_objects.json`)

| Field Name | Type | Required | Allowed Values / Format | Description |
|---|---|---|---|---|
| `id` | String | Yes | `obj-[type]-[provider]-[00000]` | Unique identifier assigned to the object. |
| `bucket_or_account` | String | Yes | e.g. `hospital-radiology-prod-aws-s3` | Cloud storage bucket or container name. |
| `cloud_provider` | Enum (String) | Yes | `AWS`, `AZURE`, `GCP` | Cloud service provider hosting the object. |
| `data_classification` | Enum (String) | Yes | `MEDICAL_IMAGE`, `APP_LOG`, `BACKUP` | Business data classification category. |
| `current_storage_class` | Enum (String) | Yes | `HOT`, `COOL`, `COLD`, `ARCHIVE`, `DEEP_ARCHIVE` | Current active storage tier. |
| `size_bytes` | Integer | Yes | `>= 0` | Size of the storage object in bytes. |
| `object_age_days` | Integer | Yes | `>= 0` | Age of object in days since creation date. |
| `last_access_days_ago` | Integer / Null | No | `>= 0` or `null` | Days since the object was last read or written. `null` indicates missing telemetry. |
| `access_frequency_30d` | Integer / Null | No | `>= 0` or `null` | Total access request count in the last 30 days. `null` indicates missing feed. |
| `restore_event_history` | List[Object] / Null | No | List of `{ "days_ago": int, "reason": str }` or `null` | Historical record of object re-hydration/restore requests. `null` indicates missing feed. |
| `retention_rule_id` | String / Null | No | e.g. `rule-hipaa-medical-image` | Foreign key reference to applicable `RetentionRule`. |
| `legal_hold` | Boolean | Yes | `true`, `false` | Compliance lock flag. If `true`, deletion and transitions are prohibited. |

---

## 2. RetentionRule Schema (`data/retention_rules.json`)

| Field Name | Type | Required | Description |
|---|---|---|---|
| `id` | String | Yes | Unique rule identifier. |
| `applies_to_classification` | Enum (String) | No | Target classification (`MEDICAL_IMAGE`, `APP_LOG`, `BACKUP`). |
| `applies_to_bucket_pattern` | String | No | Bucket glob pattern (e.g. `hospital-radiology-*`). |
| `min_retention_days` | Integer | Yes | Mandatory retention duration in days before deletion is allowed. |
| `legal_hold_override_behavior` | String | Yes | Directive on how legal hold overrides lifecycle policies (e.g. `PRESERVE_INDEFINITELY`). |
| `jurisdiction` | String | Yes | Regulatory standard (e.g. `HIPAA_US`, `HITECH_US`). |
| `description` | String | Yes | Human-readable explanation of rule requirements. |

---

## 3. Synthetic Dataset Distribution Summary

The dataset `data/synthetic_objects.json` contains 275 synthetic items:
- **Medical Images** (HIPAA 7-year retention target): 98 items
- **Application Logs** (1-year retention target): 90 items
- **Database Backups** (3-year retention target): 87 items
- **Legal Hold Active**: ~10% (27 items)
- **Recent Restore Active (< 30 days)**: ~10% (26 items)
- **Missing Access Telemetry (`access_frequency_30d == null`)**: ~15% (41 items)
- **Missing Restore Telemetry (`restore_event_history == null`)**: ~10% (28 items)
