import json
import random
from typing import List, Dict, Any

random.seed(42)  # For reproducible dataset generation

PROVIDERS = ["AWS", "AZURE", "GCP"]
CLASSIFICATIONS = ["MEDICAL_IMAGE", "APP_LOG", "BACKUP"]
STORAGE_CLASSES = ["HOT", "COOL", "COLD", "ARCHIVE", "DEEP_ARCHIVE"]

BUCKETS = {
    "AWS": {
        "MEDICAL_IMAGE": "hospital-radiology-prod-aws-s3",
        "APP_LOG": "hospital-syslog-prod-aws-s3",
        "BACKUP": "hospital-backups-dr-aws-s3"
    },
    "AZURE": {
        "MEDICAL_IMAGE": "hospitalradiologyazblob",
        "APP_LOG": "hospitalsyslogazblob",
        "BACKUP": "hospitalbackupsazblob"
    },
    "GCP": {
        "MEDICAL_IMAGE": "hospital-radiology-gcs",
        "APP_LOG": "hospital-syslog-gcs",
        "BACKUP": "hospital-backups-gcs"
    }
}

RETENTION_RULE_MAP = {
    "MEDICAL_IMAGE": "rule-hipaa-medical-image",
    "APP_LOG": "rule-app-log-retention",
    "BACKUP": "rule-backup-dr-retention"
}


def generate_objects(count: int = 275) -> List[Dict[str, Any]]:
    objects = []

    for i in range(1, count + 1):
        provider = random.choice(PROVIDERS)
        classification = random.choice(CLASSIFICATIONS)
        bucket = BUCKETS[provider][classification]
        retention_rule_id = RETENTION_RULE_MAP[classification]

        # Generate object age based on classification distribution
        if classification == "APP_LOG":
            # Logs: 1 to 500 days old
            age = random.choice([
                random.randint(1, 13),      # Very fresh (< 14d)
                random.randint(15, 29),     # 15-29d
                random.randint(30, 89),     # 30-89d
                random.randint(90, 364),    # 90-364d
                random.randint(365, 550)    # Exceeded retention (> 365d)
            ])
            size_bytes = random.randint(1_000_000, 500_000_000)  # 1MB - 500MB
        elif classification == "BACKUP":
            # Backups: 1 to 1500 days old
            age = random.choice([
                random.randint(1, 29),
                random.randint(30, 89),
                random.randint(90, 179),
                random.randint(180, 1094),
                random.randint(1095, 1400)  # Exceeded retention (> 3 years)
            ])
            size_bytes = random.randint(10_000_000_000, 500_000_000_000)  # 10GB - 500GB
        else:  # MEDICAL_IMAGE
            # Images: 1 to 3000 days old
            age = random.choice([
                random.randint(1, 29),
                random.randint(30, 89),
                random.randint(90, 364),
                random.randint(365, 2554),
                random.randint(2555, 3000)  # Exceeded HIPAA 7 year retention
            ])
            size_bytes = random.randint(50_000_000, 2_000_000_000)  # 50MB - 2GB

        # Storage class correlated with age (with some intentional misconfigurations to trigger rules)
        if age < 30:
            current_class = random.choice(["HOT", "HOT", "HOT", "COOL"])
        elif age < 90:
            current_class = random.choice(["HOT", "COOL", "COOL"])
        elif age < 365:
            current_class = random.choice(["HOT", "COOL", "COLD", "ARCHIVE"])  # HOT here is inefficient!
        else:
            current_class = random.choice(["HOT", "COOL", "COLD", "ARCHIVE", "DEEP_ARCHIVE"])

        # Determine telemetry data presence (missing data subset)
        missing_access = random.random() < 0.15   # 15% missing access frequency
        missing_restore = random.random() < 0.10  # 10% missing restore events

        if missing_access:
            access_frequency_30d = None
            last_access_days_ago = None
        else:
            if age < 30:
                access_frequency_30d = random.randint(1, 50)
                last_access_days_ago = random.randint(0, min(age, 29))
            elif age < 90:
                access_frequency_30d = random.choice([0, 0, 1, 3])
                last_access_days_ago = random.randint(5, min(age, 89))
            else:
                access_frequency_30d = random.choice([0, 0, 0, 0, 1])
                last_access_days_ago = random.randint(30, min(age, 365))

        if missing_restore:
            restore_event_history = None
        else:
            # 10% subset has a recent restore event within last 30 days
            has_recent_restore = random.random() < 0.10
            if has_recent_restore:
                restore_event_history = [
                    {"days_ago": random.randint(1, 28), "reason": "Audit request / Emergency DR drill"}
                ]
            else:
                restore_event_history = []

        # Legal hold subset (~10%)
        legal_hold = random.random() < 0.10

        obj_id = f"obj-{classification[:3].lower()}-{provider.lower()}-{i:05d}"

        objects.append({
            "id": obj_id,
            "bucket_or_account": bucket,
            "cloud_provider": provider,
            "data_classification": classification,
            "current_storage_class": current_class,
            "size_bytes": size_bytes,
            "object_age_days": age,
            "last_access_days_ago": last_access_days_ago,
            "access_frequency_30d": access_frequency_30d,
            "restore_event_history": restore_event_history,
            "retention_rule_id": retention_rule_id,
            "legal_hold": legal_hold
        })

    return objects


if __name__ == "__main__":
    data = generate_objects(275)
    with open("data/synthetic_objects.json", "w") as f:
        json.dump(data, f, indent=2)
    print(f"Successfully generated {len(data)} synthetic objects in data/synthetic_objects.json")
