import os
import logging
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

logger = logging.getLogger("uvicorn")

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

# Read database URL case-insensitively from environment variables (handles Database_Url, DATABASE_URL, etc.)
raw_db_url = None
for k, v in os.environ.items():
    if k.lower() in ("database_url", "internal_database_url", "external_database_url", "postgres_url", "postgresql_url"):
        if v and v.strip():
            raw_db_url = v.strip().strip('"\'')
            break

if raw_db_url:
    # Render provides postgres:// which SQLAlchemy 2.0 requires as postgresql://
    if raw_db_url.startswith("postgres://"):
        SQLALCHEMY_DATABASE_URL = raw_db_url.replace("postgres://", "postgresql://", 1)
    else:
        SQLALCHEMY_DATABASE_URL = raw_db_url
    engine = create_engine(SQLALCHEMY_DATABASE_URL, pool_pre_ping=True)
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



# Log database dialect directly at startup
startup_msg = f"[DB_INIT] Connected Database Backend Dialect: '{engine.dialect.name}'"
print(startup_msg)
logger.info(startup_msg)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """Dependency generator for FastAPI database sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Initializes database tables."""
    Base.metadata.create_all(bind=engine)


def get_db_backend_info():
    """Returns metadata about the active database engine backend."""
    return {
        "dialect": engine.dialect.name,
        "driver": engine.driver,
        "url_scheme": engine.url.drivername
    }

