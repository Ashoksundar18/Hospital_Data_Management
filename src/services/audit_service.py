import json
import hashlib
import datetime
from typing import Optional, Dict, Any, Tuple
from sqlalchemy.orm import Session
from src.db.db_models import AuditLogDB

GENESIS_HASH = "0000000000000000000000000000000000000000000000000000000000000000"


def compute_entry_hash(
    event_type: str,
    actor: str,
    object_id: Optional[str],
    recommendation_id: Optional[str],
    details_json: str,
    previous_hash: str
) -> str:
    """Computes SHA-256 hash for an audit log entry."""
    payload = f"{event_type}|{actor}|{object_id or ''}|{recommendation_id or ''}|{details_json}|{previous_hash}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_audit_entry(
    db: Session,
    event_type: str,
    actor: str,
    object_id: Optional[str] = None,
    recommendation_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None
) -> AuditLogDB:
    """
    Appends an immutable, cryptographic hash-chained audit log entry to the database.
    """
    details_json = json.dumps(details or {}, sort_keys=True)

    # Get last entry in the audit chain to retrieve previous_hash
    last_entry = db.query(AuditLogDB).order_by(AuditLogDB.id.desc()).first()
    previous_hash = last_entry.entry_hash if last_entry else GENESIS_HASH

    entry_hash = compute_entry_hash(
        event_type=event_type,
        actor=actor,
        object_id=object_id,
        recommendation_id=recommendation_id,
        details_json=details_json,
        previous_hash=previous_hash
    )

    audit_entry = AuditLogDB(
        timestamp=datetime.datetime.utcnow(),
        event_type=event_type,
        actor=actor,
        object_id=object_id,
        recommendation_id=recommendation_id,
        details_json=details_json,
        previous_hash=previous_hash,
        entry_hash=entry_hash
    )

    db.add(audit_entry)
    db.commit()
    db.refresh(audit_entry)
    return audit_entry


def verify_audit_chain(db: Session) -> Tuple[bool, Optional[int], str]:
    """
    Verifies the cryptographic hash chain integrity across all audit log entries.
    Returns (is_valid: bool, tampered_entry_id: Optional[int], message: str).
    """
    entries = db.query(AuditLogDB).order_by(AuditLogDB.id.asc()).all()
    if not entries:
        return True, None, "Audit trail is empty. Chain is valid."

    expected_previous_hash = GENESIS_HASH

    for entry in entries:
        # Check 1: Does entry's recorded previous_hash match expected previous hash?
        if entry.previous_hash != expected_previous_hash:
            return (
                False,
                entry.id,
                f"Tampering detected at Audit Entry #{entry.id}: previous_hash mismatch. Expected {expected_previous_hash}, got {entry.previous_hash}."
            )

        # Check 2: Recompute entry's own SHA-256 hash signature
        recomputed_hash = compute_entry_hash(
            event_type=entry.event_type,
            actor=entry.actor,
            object_id=entry.object_id,
            recommendation_id=entry.recommendation_id,
            details_json=entry.details_json,
            previous_hash=entry.previous_hash
        )

        if entry.entry_hash != recomputed_hash:
            return (
                False,
                entry.id,
                f"Tampering detected at Audit Entry #{entry.id}: entry_hash payload content altered. Recorded {entry.entry_hash}, recomputed {recomputed_hash}."
            )

        # Chain advances to current entry's hash
        expected_previous_hash = entry.entry_hash

    return True, None, f"Audit chain integrity verified. All {len(entries)} entries are untampered and cryptographically valid."
