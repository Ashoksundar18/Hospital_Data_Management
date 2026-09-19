import os
import logging
from typing import Mapping, Optional
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base

from src.logging_config import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

# Parse local .env file if present
env_path = os.path.join(os.getcwd(), ".env")
if os.path.exists(env_path):
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"\''))
    except Exception as e:
        logger.warning(f"Could not load .env file: {e}")

PRIORITY_KEYS = [
    "database_url",
    "internal_database_url",
    "external_database_url",
    "postgres_url",
    "postgresql_url",
]


def resolve_database_url(environ: Mapping[str, str]) -> Optional[str]:
    """
    Extracts and normalizes the target database URL from an environment mapping in a
    deterministic priority order. Performs case-insensitive key lookup and skips empty/whitespace values.
    """
    env_lower = {k.lower(): v for k, v in environ.items() if v is not None}

    for key in PRIORITY_KEYS:
        val = env_lower.get(key)
        if val is not None:
            stripped = val.strip().strip('"\'')
            if stripped:
                if stripped.startswith("postgres://"):
                    return stripped.replace("postgres://", "postgresql://", 1)
                return stripped
    return None


raw_db_url = resolve_database_url(os.environ)
is_render = bool(os.getenv("RENDER"))

if raw_db_url:
    engine = create_engine(raw_db_url, pool_pre_ping=True)
elif is_render:
    raise RuntimeError("DATABASE_URL environment variable is required when running on Render, but was missing or empty.")
else:
    # Fallback to local SQLite database for local development
    DB_DIR = os.path.join("data")
    os.makedirs(DB_DIR, exist_ok=True)
    DB_PATH = os.path.join(DB_DIR, "hospital_lifecycle.db")
    SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL,
        connect_args={"check_same_thread": False}
    )

# Single standardized log line for database initialization
logger.info(f"[DB_INIT] dialect={engine.dialect.name} driver={engine.driver}")



SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """Dependency generator for FastAPI database sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_indexes(target_engine=None):
    """
    Idempotently ensures key indexes exist on the database tables.
    Executes CREATE INDEX IF NOT EXISTS for PostgreSQL and SQLite.
    """
    eng = target_engine or engine
    indexes_to_create = [
        ("ix_audit_log_object_id", "audit_log", "object_id"),
        ("ix_recommendations_approval_status", "recommendations", "approval_status"),
        ("ix_confirmations_and_overrides_object_id", "confirmations_and_overrides", "object_id"),
    ]
    with eng.connect() as conn:
        for idx_name, table_name, col_name in indexes_to_create:
            try:
                conn.execute(
                    text(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table_name} ({col_name})")
                )
            except Exception as e:
                logger.warning(f"Could not create index {idx_name} on {table_name}: {e}")
        conn.commit()


def init_db(target_engine=None):
    """Initializes database tables and ensures indexes are present."""
    eng = target_engine or engine
    Base.metadata.create_all(bind=eng)
    ensure_indexes(eng)


def get_db_backend_info():
    """Returns metadata about the active database engine backend."""
    return {
        "dialect": engine.dialect.name,
        "driver": engine.driver,
        "url_scheme": engine.url.drivername
    }

