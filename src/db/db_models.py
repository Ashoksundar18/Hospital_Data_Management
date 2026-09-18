import datetime
from sqlalchemy import Column, String, Integer, BigInteger, Boolean, Text, DateTime, ForeignKey
from .database import Base


class StorageObjectDB(Base):
    __tablename__ = "storage_objects"

    id = Column(String, primary_key=True, index=True)
    bucket_or_account = Column(String, nullable=False)
    cloud_provider = Column(String, nullable=False)
    data_classification = Column(String, nullable=False)
    current_storage_class = Column(String, nullable=False)
    size_bytes = Column(BigInteger, nullable=False)
    object_age_days = Column(Integer, nullable=False)
    last_access_days_ago = Column(Integer, nullable=True)
    access_frequency_30d = Column(Integer, nullable=True)
    restore_event_history_json = Column(Text, nullable=True)
    retention_rule_id = Column(String, nullable=True)
    legal_hold = Column(Boolean, default=False, nullable=False)


class RetentionRuleDB(Base):
    __tablename__ = "retention_rules"

    id = Column(String, primary_key=True, index=True)
    applies_to_classification = Column(String, nullable=True)
    applies_to_bucket_pattern = Column(String, nullable=True)
    min_retention_days = Column(Integer, nullable=False)
    min_retrieval_tier = Column(String, nullable=True)
    max_retrieval_latency_hours = Column(Integer, nullable=True)
    legal_hold_override_behavior = Column(String, default="PRESERVE_INDEFINITELY")
    jurisdiction = Column(String, default="HIPAA_US")
    description = Column(Text, nullable=False)


class RecommendationDB(Base):
    __tablename__ = "recommendations"

    id = Column(String, primary_key=True, index=True)
    object_id = Column(String, ForeignKey("storage_objects.id"), index=True, nullable=False)
    current_class = Column(String, nullable=False)
    recommended_action = Column(String, nullable=False)
    target_storage_class = Column(String, nullable=True)
    triggering_rules_json = Column(Text, nullable=False)
    evidence_snapshot_json = Column(Text, nullable=False)
    confidence_tier = Column(String, nullable=False)
    impact_tier = Column(String, nullable=False)
    data_completeness_flag = Column(String, nullable=False)
    approval_status = Column(String, default="pending", nullable=False)  # pending, confirmed, overridden, rolled_back
    requires_periodic_review = Column(Boolean, default=False, nullable=False)
    last_reviewed_at = Column(DateTime, nullable=True)
    reasoning_summary = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)


class ConfirmationOverrideDB(Base):
    __tablename__ = "confirmations_and_overrides"

    id = Column(String, primary_key=True, index=True)
    recommendation_id = Column(String, ForeignKey("recommendations.id"), index=True, nullable=False)
    object_id = Column(String, nullable=False)
    action_type = Column(String, nullable=False)  # CONFIRM, OVERRIDE, PERIODIC_REVIEW, ROLLBACK
    reviewer_id = Column(String, nullable=False)
    override_reason_taxonomy = Column(String, nullable=True)
    other_reason_text = Column(Text, nullable=True)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)


class AuditLogDB(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    event_type = Column(String, nullable=False)  # INGEST_OBJECT, GENERATE_RECOMMENDATION, CONFIRM_RECOMMENDATION, etc.
    actor = Column(String, nullable=False)
    object_id = Column(String, nullable=True)
    recommendation_id = Column(String, nullable=True)
    details_json = Column(Text, nullable=False)
    previous_hash = Column(String, nullable=False)
    entry_hash = Column(String, nullable=False)
