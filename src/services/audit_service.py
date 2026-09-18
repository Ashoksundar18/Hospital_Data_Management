import json
import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple
from sqlalchemy import text
from sqlalchemy.orm import Session
from src.db.db_models import AuditLogDB

logger = logging.getLogger(__name__)

GENESIS_HASH = "0000000000000000000000000000000000000000000000000000000000000000"
AUDIT_ADVISORY_LOCK_ID = 482910472  # Constant 64-bit integer key for Postgres transaction advisory lock


def compute_entry_hash(
    event_type: str,
    actor: str,
    object_id: Optional[str],
    recommendation_id: Optional[str],
    details_json: str,
    previous_hash: str
) -> str:
    """
    Computes SHA-256 hash for an audit log entry.
    NOTE: 'id' and 'timestamp' are deliberately omitted from the SHA-256 payload string
    to preserve 100% backward compatibility with pre-existing production audit log entries.
    """
    payload = f"{event_type}|{actor}|{object_id or ''}|{recommendation_id or ''}|{details_json}|{previous_hash}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_audit_entry(
    db: Session,
    event_type: str,
    actor: str,
    object_id: Optional[str] = None,
    recommendation_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    auto_commit: bool = True
) -> AuditLogDB:
    """
    Appends an immutable, cryptographic hash-chained audit log entry to the database.
    Acquires a PostgreSQL transaction-level advisory lock on Postgres backends to prevent chain forks.
    """
    # Acquire transaction-level advisory lock on PostgreSQL to prevent concurrent append race conditions
    if db.bind and db.bind.dialect.name == "postgresql":
        db.execute(text(f"SELECT pg_advisory_xact_lock({AUDIT_ADVISORY_LOCK_ID})"))

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
        timestamp=datetime.now(timezone.utc),
        event_type=event_type,
        actor=actor,
        object_id=object_id,
        recommendation_id=recommendation_id,
        details_json=details_json,
        previous_hash=previous_hash,
        entry_hash=entry_hash
    )

    db.add(audit_entry)
    if auto_commit:
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
        if entry.previous_hash != expected_previous_hash:
            return (
                False,
                entry.id,
                f"Tampering detected at Audit Entry #{entry.id}: previous_hash mismatch. Expected {expected_previous_hash}, got {entry.previous_hash}."
            )

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

        expected_previous_hash = entry.entry_hash

    return True, None, f"Audit chain integrity verified. All {len(entries)} entries are untampered and cryptographically valid."
