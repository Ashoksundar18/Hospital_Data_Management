import pytest
from src.models import (
    StorageObject,
    CloudProvider,
    DataClassification,
    StorageClass,
    RetentionRule,
    RecommendedAction,
    ConfidenceTier,
    ImpactTier,
    DataCompletenessFlag,
)
from src.engine import LifecycleRulesEngine


@pytest.fixture
def sample_retention_rules():
    return [
        RetentionRule(
            id="rule-hipaa-medical-image",
            applies_to_classification=DataClassification.MEDICAL_IMAGE,
            min_retention_days=2555,  # 7 years
            description="HIPAA minimum retention"
        ),
        RetentionRule(
            id="rule-app-log",
            applies_to_classification=DataClassification.APP_LOG,
            min_retention_days=365,   # 1 year
            description="App log retention"
        ),
    ]


def test_normal_case_tier_transition(sample_retention_rules):
    """1. Normal Case: Object is eligible for storage tier transition."""
    engine = LifecycleRulesEngine(retention_rules=sample_retention_rules)

    obj = StorageObject(
        id="test-obj-normal-01",
        bucket_or_account="hospital-radiology-bucket",
        cloud_provider=CloudProvider.AWS,
        data_classification=DataClassification.MEDICAL_IMAGE,
        current_storage_class=StorageClass.HOT,
        size_bytes=50000000,
        object_age_days=120,
        access_frequency_30d=0,
        restore_event_history=[],
        retention_rule_id="rule-hipaa-medical-image",
        legal_hold=False
    )

    rec = engine.evaluate_object(obj)

    assert rec.recommended_action == RecommendedAction.TRANSITION
    assert rec.target_storage_class == StorageClass.COOL
    assert "RULE_AGE_ACCESS_TRANSITION" in rec.triggering_rules
    assert rec.confidence_tier == ConfidenceTier.HIGH
    assert rec.data_completeness_flag == DataCompletenessFlag.COMPLETE


def test_legal_hold_confidence_is_always_high(sample_retention_rules):
    """
    2. Legal-Hold Bug Fix Test: Legal hold recommendations MUST ALWAYS be HIGH confidence,
    even when telemetry (access frequency / restore history) is missing.
    """
    engine = LifecycleRulesEngine(retention_rules=sample_retention_rules)

    obj = StorageObject(
        id="obj-app-gcp-00004",
        bucket_or_account="hospital-syslog-gcs",
        cloud_provider=CloudProvider.GCP,
        data_classification=DataClassification.APP_LOG,
        current_storage_class=StorageClass.HOT,
        size_bytes=500000000,
        object_age_days=100,
        access_frequency_30d=None,     # MISSING FEED
        restore_event_history=None,    # MISSING FEED
        retention_rule_id="rule-app-log",
        legal_hold=True
    )

    rec = engine.evaluate_object(obj)

    assert rec.recommended_action == RecommendedAction.NO_ACTION
    assert rec.target_storage_class is None
    assert "RULE_LEGAL_HOLD" in rec.triggering_rules
    assert rec.confidence_tier == ConfidenceTier.HIGH
    assert rec.data_completeness_flag == DataCompletenessFlag.INCOMPLETE


def test_retention_lock_blocks_deletion(sample_retention_rules):
    """3. Retention Lock Case: Engine refuses to recommend deletion if age < min_retention_days."""
    engine = LifecycleRulesEngine(retention_rules=sample_retention_rules)

    obj = StorageObject(
        id="test-retention-lock-01",
        bucket_or_account="hospital-radiology-bucket",
        cloud_provider=CloudProvider.AWS,
        data_classification=DataClassification.MEDICAL_IMAGE,
        current_storage_class=StorageClass.HOT,
        size_bytes=50000000,
        object_age_days=500,
        access_frequency_30d=0,
        restore_event_history=[],
        retention_rule_id="rule-hipaa-medical-image",
        legal_hold=False
    )

    rec = engine.evaluate_object(obj)

    assert rec.recommended_action != RecommendedAction.DELETE
    assert rec.confidence_tier == ConfidenceTier.HIGH


def test_rule_no_action_default(sample_retention_rules):
    """4. RULE_NO_ACTION_DEFAULT: Fires when an object is already appropriately tiered."""
    engine = LifecycleRulesEngine(retention_rules=sample_retention_rules)

    obj = StorageObject(
        id="test-no-action-01",
        bucket_or_account="hospital-radiology-bucket",
        cloud_provider=CloudProvider.AWS,
        data_classification=DataClassification.MEDICAL_IMAGE,
        current_storage_class=StorageClass.HOT,
        size_bytes=50000000,
        object_age_days=15,
        access_frequency_30d=10,
        restore_event_history=[],
        retention_rule_id="rule-hipaa-medical-image",
        legal_hold=False
    )

    rec = engine.evaluate_object(obj)

    assert rec.recommended_action == RecommendedAction.NO_ACTION
    assert "RULE_NO_ACTION_DEFAULT" in rec.triggering_rules


def test_impact_tier_precedence_calculation(sample_retention_rules):
    """
    5. Precedence-based Impact Tiering Test:
    - Step 1: NO_ACTION action -> LOW_IMPACT (even for MEDICAL_IMAGE like obj-med-gcp-00001)
    - Step 2: DELETE action -> HIGH_IMPACT
    - Step 3b: TRANSITION of MEDICAL_IMAGE (obj-med-aws-00005) -> HIGH_IMPACT
    """
    engine = LifecycleRulesEngine(retention_rules=sample_retention_rules)

    # Test 5a: Precedence 1 - MEDICAL_IMAGE with NO_ACTION (e.g. obj-med-gcp-00001) -> LOW_IMPACT
    obj_no_action_med = StorageObject(
        id="obj-med-gcp-00001", bucket_or_account="b", cloud_provider=CloudProvider.GCP,
        data_classification=DataClassification.MEDICAL_IMAGE, current_storage_class=StorageClass.HOT,
        size_bytes=1000, object_age_days=10, access_frequency_30d=5, restore_event_history=[],
        retention_rule_id="rule-hipaa-medical-image", legal_hold=False
    )
    rec_5a = engine.evaluate_object(obj_no_action_med)
    assert rec_5a.recommended_action == RecommendedAction.NO_ACTION
    assert rec_5a.impact_tier == ImpactTier.LOW_IMPACT  # Precedence Step 1 overrides classification!

    # Test 5b: Precedence 2 - DELETE action -> HIGH_IMPACT
    obj_del = StorageObject(
        id="test-del", bucket_or_account="b", cloud_provider=CloudProvider.AWS,
        data_classification=DataClassification.APP_LOG, current_storage_class=StorageClass.ARCHIVE,
        size_bytes=1000, object_age_days=400, access_frequency_30d=0, restore_event_history=[],
        retention_rule_id="rule-app-log", legal_hold=False
    )
    assert engine.evaluate_object(obj_del).impact_tier == ImpactTier.HIGH_IMPACT

    # Test 5c: Precedence 3b - TRANSITION of MEDICAL_IMAGE (obj-med-aws-00005) -> HIGH_IMPACT
    obj_img_trans = StorageObject(
        id="obj-med-aws-00005", bucket_or_account="b", cloud_provider=CloudProvider.AWS,
        data_classification=DataClassification.MEDICAL_IMAGE, current_storage_class=StorageClass.COOL,
        size_bytes=1000, object_age_days=95, access_frequency_30d=0, restore_event_history=[],
        retention_rule_id="rule-hipaa-medical-image", legal_hold=False
    )
    rec_5c = engine.evaluate_object(obj_img_trans)
    assert rec_5c.recommended_action == RecommendedAction.TRANSITION
    assert rec_5c.impact_tier == ImpactTier.HIGH_IMPACT  # Precedence Step 3b


def test_double_missing_telemetry_fallback(sample_retention_rules):
    """
    6. Double-Missing Telemetry Test: Verified fallback when BOTH access frequency
    and restore history feeds are None.
    """
    engine = LifecycleRulesEngine(retention_rules=sample_retention_rules)

    obj = StorageObject(
        id="obj-bac-azure-00088",
        bucket_or_account="hospitalbackupsazblob",
        cloud_provider=CloudProvider.AZURE,
        data_classification=DataClassification.BACKUP,
        current_storage_class=StorageClass.HOT,
        size_bytes=20000000000,
        object_age_days=45,
        access_frequency_30d=None,
        restore_event_history=None,
        retention_rule_id="rule-backup-dr-retention",
        legal_hold=False
    )

    rec = engine.evaluate_object(obj)

    assert rec.recommended_action == RecommendedAction.TRANSITION
    assert rec.target_storage_class == StorageClass.COOL
    assert rec.confidence_tier == ConfidenceTier.LOW
    assert rec.data_completeness_flag == DataCompletenessFlag.INCOMPLETE
    assert "RULE_MISSING_DATA_FALLBACK" in rec.triggering_rules
