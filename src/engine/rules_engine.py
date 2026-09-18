from typing import List, Optional, Dict, Tuple, Any
from src.models.storage_object import StorageObject, StorageClass, DataClassification
from src.models.retention_rule import RetentionRule
from src.models.recommendation import (
    Recommendation,
    RecommendedAction,
    ConfidenceTier,
    ImpactTier,
    DataCompletenessFlag,
    ApprovalStatus,
)

# Ordered progression of storage tiers from warmest (index 0) to coldest (index 4)
TIER_ORDER = [
    StorageClass.HOT,
    StorageClass.COOL,
    StorageClass.COLD,
    StorageClass.ARCHIVE,
    StorageClass.DEEP_ARCHIVE,
]

LARGE_OBJECT_THRESHOLD_BYTES = 50 * 1024 * 1024 * 1024  # 50 GB


class LifecycleRulesEngine:
    """
    Deterministic, explainable storage lifecycle recommendation engine (Phase 2).
    
    Rule Catalog:
    1. RULE_LEGAL_HOLD: Hard compliance lock. Legal hold forces NO_ACTION.
       Confidence: ALWAYS HIGH (does not depend on access/restore telemetry).
       Governance: Sets requires_periodic_review = True.
    2. RULE_RETENTION_LOCK: Blocks DELETE recommendations if age < min_retention_days.
       Confidence: ALWAYS HIGH.
       Governance: Sets requires_periodic_review = True for NO_ACTION outcomes.
    3. RULE_RETRIEVAL_SLA (Phase 2): Blocks or downgrades transitions to tiers colder than
       min_retrieval_tier specified by applicable RetentionRule.
       Confidence: ALWAYS HIGH.
    4. RULE_EXPIRATION_DELETE: Recommends DELETE if age >= max_lifecycle_days and retention is satisfied.
       Confidence: ALWAYS HIGH.
    5. RULE_RECENT_RESTORE: Defers colder tier transitions if object was restored within 30 days.
       Confidence: LOW if restore telemetry is missing; HIGH if complete.
    6. RULE_AGE_ACCESS_TRANSITION: Recommends step-down storage class transitions based on age & access frequency.
       Confidence: LOW if access telemetry is missing (uses age fallback path).
    7. RULE_NO_ACTION_DEFAULT: Issued when no tier change or deletion is warranted.
       Confidence: HIGH if telemetry complete; LOW if telemetry missing.
    """

    def __init__(self, retention_rules: Optional[List[RetentionRule]] = None):
        self.retention_rules = retention_rules or []

    def find_applicable_retention_rule(self, obj: StorageObject) -> Optional[RetentionRule]:
        if obj.retention_rule_id:
            for rule in self.retention_rules:
                if rule.id == obj.retention_rule_id:
                    return rule
        
        for rule in self.retention_rules:
            if rule.applies_to_classification == obj.data_classification:
                return rule
                
        return None

    def calculate_impact_tier(
        self,
        action: RecommendedAction,
        target_class: Optional[StorageClass],
        obj: StorageObject
    ) -> ImpactTier:
        if action == RecommendedAction.NO_ACTION:
            return ImpactTier.LOW_IMPACT

        if action == RecommendedAction.DELETE:
            return ImpactTier.HIGH_IMPACT

        # Action is TRANSITION
        if target_class in [StorageClass.ARCHIVE, StorageClass.DEEP_ARCHIVE]:
            return ImpactTier.HIGH_IMPACT

        if obj.data_classification == DataClassification.MEDICAL_IMAGE:
            return ImpactTier.HIGH_IMPACT

        if obj.size_bytes >= LARGE_OBJECT_THRESHOLD_BYTES:
            return ImpactTier.HIGH_IMPACT

        return ImpactTier.MEDIUM_IMPACT

    def evaluate_object(self, obj: StorageObject) -> Recommendation:
        missing_access = obj.access_frequency_30d is None
        missing_restore = obj.restore_event_history is None

        if missing_access and missing_restore:
            completeness = DataCompletenessFlag.INCOMPLETE
        elif missing_access:
            completeness = DataCompletenessFlag.PARTIAL_MISSING_ACCESS
        elif missing_restore:
            completeness = DataCompletenessFlag.PARTIAL_MISSING_RESTORE
        else:
            completeness = DataCompletenessFlag.COMPLETE

        applicable_rule = self.find_applicable_retention_rule(obj)
        min_retention_days = applicable_rule.min_retention_days if applicable_rule else 0

        recent_restore_detected = False
        if obj.restore_event_history:
            recent_restore_detected = any(event.days_ago <= 30 for event in obj.restore_event_history)

        evidence = {
            "object_id": obj.id,
            "cloud_provider": obj.cloud_provider.value,
            "data_classification": obj.data_classification.value,
            "current_storage_class": obj.current_storage_class.value,
            "object_age_days": obj.object_age_days,
            "last_access_days_ago": obj.last_access_days_ago,
            "access_frequency_30d": obj.access_frequency_30d,
            "has_recent_restore_30d": recent_restore_detected,
            "legal_hold": obj.legal_hold,
            "retention_rule_id": applicable_rule.id if applicable_rule else None,
            "min_retention_days": min_retention_days,
            "min_retrieval_tier": applicable_rule.min_retrieval_tier.value if (applicable_rule and applicable_rule.min_retrieval_tier) else None,
            "max_retrieval_latency_hours": applicable_rule.max_retrieval_latency_hours if applicable_rule else None,
            "retention_satisfied": obj.object_age_days >= min_retention_days,
            "missing_access_data": missing_access,
            "missing_restore_data": missing_restore,
        }

        # RULE 1: Legal Hold Check (Hard compliance invariant)
        # Periodic Review: Compliance-sensitive NO_ACTION items set requires_periodic_review = True
        if obj.legal_hold:
            return Recommendation(
                object_id=obj.id,
                current_class=obj.current_storage_class,
                recommended_action=RecommendedAction.NO_ACTION,
                target_storage_class=None,
                triggering_rules=["RULE_LEGAL_HOLD"],
                evidence_snapshot=evidence,
                confidence_tier=ConfidenceTier.HIGH,
                impact_tier=ImpactTier.LOW_IMPACT,
                data_completeness_flag=completeness,
                approval_status=ApprovalStatus.PENDING,
                requires_periodic_review=True,  # Phase 2 Compliance Review Flag
                reasoning_summary="Object is under legal hold. Deletion and storage class transitions are strictly prohibited by compliance policy."
            )

        max_lifecycle_days = {
            DataClassification.APP_LOG: 365,          # 1 year
            DataClassification.BACKUP: 1095,         # 3 years
            DataClassification.MEDICAL_IMAGE: 2555,  # 7 years
        }.get(obj.data_classification, 1095)

        retention_satisfied = obj.object_age_days >= min_retention_days
        age_exceeds_max_lifecycle = obj.object_age_days >= max_lifecycle_days

        # RULE 2: Expiration / Deletion & Retention Lock Evaluation
        if age_exceeds_max_lifecycle:
            if not retention_satisfied:
                evidence["deletion_prevented_by_retention"] = True
                return Recommendation(
                    object_id=obj.id,
                    current_class=obj.current_storage_class,
                    recommended_action=RecommendedAction.NO_ACTION,
                    target_storage_class=None,
                    triggering_rules=["RULE_RETENTION_LOCK"],
                    evidence_snapshot=evidence,
                    confidence_tier=ConfidenceTier.HIGH,
                    impact_tier=ImpactTier.LOW_IMPACT,
                    data_completeness_flag=completeness,
                    approval_status=ApprovalStatus.PENDING,
                    requires_periodic_review=True,  # Phase 2 Compliance Review Flag
                    reasoning_summary=f"Object age ({obj.object_age_days}d) exceeds maximum lifecycle policy, but minimum retention period ({min_retention_days}d) is not satisfied. Deletion blocked by retention rule '{applicable_rule.id if applicable_rule else 'default'}'."
                )
            else:
                return Recommendation(
                    object_id=obj.id,
                    current_class=obj.current_storage_class,
                    recommended_action=RecommendedAction.DELETE,
                    target_storage_class=None,
                    triggering_rules=["RULE_EXPIRATION_DELETE"],
                    evidence_snapshot=evidence,
                    confidence_tier=ConfidenceTier.HIGH,
                    impact_tier=ImpactTier.HIGH_IMPACT,
                    data_completeness_flag=completeness,
                    approval_status=ApprovalStatus.PENDING,
                    requires_periodic_review=False,
                    reasoning_summary=f"Object age ({obj.object_age_days}d) exceeds lifecycle expiration threshold ({max_lifecycle_days}d) and retention requirement ({min_retention_days}d) is satisfied. Recommended for deletion."
                )

        # RULE 3: Recent Restore Event Protection
        if recent_restore_detected:
            restore_confidence = ConfidenceTier.LOW if missing_restore else ConfidenceTier.HIGH
            return Recommendation(
                object_id=obj.id,
                current_class=obj.current_storage_class,
                recommended_action=RecommendedAction.NO_ACTION,
                target_storage_class=None,
                triggering_rules=["RULE_RECENT_RESTORE"] if not missing_restore else ["RULE_RECENT_RESTORE", "RULE_MISSING_DATA_FALLBACK"],
                evidence_snapshot=evidence,
                confidence_tier=restore_confidence,
                impact_tier=ImpactTier.LOW_IMPACT,
                data_completeness_flag=completeness,
                approval_status=ApprovalStatus.PENDING,
                requires_periodic_review=False,
                reasoning_summary="Object was restored within the last 30 days. Transition to colder storage class is deferred to prevent re-access performance penalties."
            )

        # RULE 4: Tier Transition & RULE_RETRIEVAL_SLA Evaluation
        candidate_tier, trigger_rule, transition_reason = self._evaluate_tier_transition(obj, missing_access)

        if candidate_tier and candidate_tier != obj.current_storage_class:
            final_target_tier = candidate_tier
            retrieval_sla_triggered = False

            # Phase 2 RULE_RETRIEVAL_SLA check
            if applicable_rule and applicable_rule.min_retrieval_tier:
                min_tier = applicable_rule.min_retrieval_tier
                curr_idx = TIER_ORDER.index(obj.current_storage_class)
                cand_idx = TIER_ORDER.index(candidate_tier)
                min_idx = TIER_ORDER.index(min_tier)

                if cand_idx > min_idx:
                    # Candidate tier is colder than allowed min_retrieval_tier
                    retrieval_sla_triggered = True
                    if curr_idx < min_idx:
                        # Downgrade target to min_retrieval_tier
                        final_target_tier = min_tier
                        transition_reason = f"Transition downgraded to {min_tier.value}: Candidate tier {candidate_tier.value} violates retention rule retrieval SLA constraint (min_retrieval_tier: {min_tier.value})."
                    else:
                        # Current class is already at or colder than min_retrieval_tier -> block further transition
                        final_target_tier = None

            if final_target_tier is None:
                # Transition blocked by RULE_RETRIEVAL_SLA
                return Recommendation(
                    object_id=obj.id,
                    current_class=obj.current_storage_class,
                    recommended_action=RecommendedAction.NO_ACTION,
                    target_storage_class=None,
                    triggering_rules=["RULE_RETRIEVAL_SLA"],
                    evidence_snapshot=evidence,
                    confidence_tier=ConfidenceTier.HIGH,
                    impact_tier=ImpactTier.LOW_IMPACT,
                    data_completeness_flag=completeness,
                    approval_status=ApprovalStatus.PENDING,
                    requires_periodic_review=False,
                    reasoning_summary=f"Storage class transition blocked by retention rule retrieval SLA: Object is in {obj.current_storage_class.value} and may not go colder than {applicable_rule.min_retrieval_tier.value}."
                )

            trigger_list = ["RULE_RETRIEVAL_SLA" if retrieval_sla_triggered else trigger_rule]
            rule_confidence = ConfidenceTier.HIGH
            if missing_access or missing_restore:
                trigger_list.append("RULE_MISSING_DATA_FALLBACK")
                rule_confidence = ConfidenceTier.LOW
                transition_reason += f" (Note: Evaluated under conservative fallback due to missing telemetry: {completeness.value})."

            impact = self.calculate_impact_tier(RecommendedAction.TRANSITION, final_target_tier, obj)

            return Recommendation(
                object_id=obj.id,
                current_class=obj.current_storage_class,
                recommended_action=RecommendedAction.TRANSITION,
                target_storage_class=final_target_tier,
                triggering_rules=trigger_list,
                evidence_snapshot=evidence,
                confidence_tier=rule_confidence,
                impact_tier=impact,
                data_completeness_flag=completeness,
                approval_status=ApprovalStatus.PENDING,
                requires_periodic_review=False,
                reasoning_summary=transition_reason
            )

        # RULE 5: Default No Action Required
        default_confidence = ConfidenceTier.LOW if (missing_access or missing_restore) else ConfidenceTier.HIGH
        default_summary = f"Object is appropriately tiered in {obj.current_storage_class.value} (age: {obj.object_age_days}d)."
        if missing_access or missing_restore:
            default_summary += f" Evaluated with lower confidence due to missing telemetry ({completeness.value})."

        return Recommendation(
            object_id=obj.id,
            current_class=obj.current_storage_class,
            recommended_action=RecommendedAction.NO_ACTION,
            target_storage_class=None,
            triggering_rules=["RULE_NO_ACTION_DEFAULT"] if not (missing_access or missing_restore) else ["RULE_NO_ACTION_DEFAULT", "RULE_MISSING_DATA_FALLBACK"],
            evidence_snapshot=evidence,
            confidence_tier=default_confidence,
            impact_tier=ImpactTier.LOW_IMPACT,
            data_completeness_flag=completeness,
            approval_status=ApprovalStatus.PENDING,
            requires_periodic_review=False,
            reasoning_summary=default_summary
        )

    def _evaluate_tier_transition(self, obj: StorageObject, missing_access: bool) -> Tuple[Optional[StorageClass], str, str]:
        curr = obj.current_storage_class
        age = obj.object_age_days
        freq = obj.access_frequency_30d if not missing_access else 0

        if not missing_access and obj.access_frequency_30d and obj.access_frequency_30d >= 5:
            return None, "", ""

        if curr == StorageClass.HOT:
            if age >= 30:
                reason = f"Object age ({age}d) >= 30 days and access frequency in past 30 days is {obj.access_frequency_30d if not missing_access else 'unknown (missing feed)'}."
                return StorageClass.COOL, "RULE_AGE_ACCESS_TRANSITION", f"Recommend transition from HOT to COOL: {reason}"

        elif curr == StorageClass.COOL:
            if age >= 90:
                if missing_access and age < 180:
                    return None, "", ""
                target = StorageClass.COLD if obj.data_classification == DataClassification.MEDICAL_IMAGE else StorageClass.ARCHIVE
                reason = f"Object age ({age}d) >= 90 days with low access activity ({freq} accesses/30d)."
                return target, "RULE_AGE_ACCESS_TRANSITION", f"Recommend transition from COOL to {target.value}: {reason}"

        elif curr == StorageClass.COLD:
            if age >= 180:
                if missing_access and age < 365:
                    return None, "", ""
                target = StorageClass.DEEP_ARCHIVE if obj.data_classification == DataClassification.BACKUP else StorageClass.ARCHIVE
                reason = f"Object age ({age}d) >= 180 days."
                return target, "RULE_AGE_ACCESS_TRANSITION", f"Recommend transition from COLD to {target.value}: {reason}"

        elif curr == StorageClass.ARCHIVE:
            if age >= 365:
                reason = f"Object age ({age}d) >= 365 days in Archive."
                return StorageClass.DEEP_ARCHIVE, "RULE_AGE_ACCESS_TRANSITION", f"Recommend transition from ARCHIVE to DEEP_ARCHIVE: {reason}"

        return None, "", ""

    def evaluate_batch(self, objects: List[StorageObject]) -> List[Recommendation]:
        return [self.evaluate_object(obj) for obj in objects]
