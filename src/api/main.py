import json
import os
from typing import List, Optional, Dict
from fastapi import FastAPI, HTTPException, Query, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse

from src.models import (
    StorageObject,
    RetentionRule,
    Recommendation,
    RecommendedAction,
    DataClassification
)
from src.engine import LifecycleRulesEngine

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    load_initial_data()
    yield

app = FastAPI(
    title="Storage Lifecycle Recommender API",
    description="Responsible-AI automated storage lifecycle recommender for healthcare multi-cloud storage.",
    version="1.0.0",
    lifespan=lifespan
)

# In-memory storage stores loaded synthetic objects and retention rules
OBJECTS_DB: Dict[str, StorageObject] = {}
RETENTION_RULES_DB: List[RetentionRule] = []


def load_initial_data():
    """Loads retention rules and synthetic dataset into memory on startup."""
    global OBJECTS_DB, RETENTION_RULES_DB
    
    # Load retention rules
    retention_path = os.path.join("data", "retention_rules.json")
    if os.path.exists(retention_path):
        with open(retention_path, "r") as f:
            rules_raw = json.load(f)
            RETENTION_RULES_DB = [RetentionRule(**r) for r in rules_raw]

    # Load synthetic objects
    objects_path = os.path.join("data", "synthetic_objects.json")
    if os.path.exists(objects_path):
        with open(objects_path, "r") as f:
            objects_raw = json.load(f)
            for item in objects_raw:
                obj = StorageObject(**item)
                OBJECTS_DB[obj.id] = obj


# Mount UI static files if directory exists
if os.path.exists("ui"):
    app.mount("/static", StaticFiles(directory="ui"), name="static")


@app.get("/", response_class=HTMLResponse)
def root_ui():
    """Serves prototype UI dashboard index file."""
    index_path = os.path.join("ui", "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Storage Lifecycle Recommender API Running</h1><p>Visit /docs for API documentation.</p>"


@app.post("/api/v1/objects", status_code=status.HTTP_201_CREATED)
def ingest_object(obj: StorageObject):
    """
    Ingests or updates a storage object's metadata.
    """
    OBJECTS_DB[obj.id] = obj
    return {
        "status": "success",
        "message": "Storage object metadata ingested successfully",
        "object_id": obj.id
    }


@app.get("/api/v1/objects", response_model=List[StorageObject])
def list_objects(
    classification: Optional[DataClassification] = None,
    legal_hold: Optional[bool] = None
):
    """
    Retrieves list of ingested storage objects with optional filters.
    """
    results = list(OBJECTS_DB.values())
    if classification:
        results = [o for o in results if o.data_classification == classification]
    if legal_hold is not None:
        results = [o for o in results if o.legal_hold == legal_hold]
    return results


@app.get("/api/v1/recommendations", response_model=List[Recommendation])
def get_recommendations(
    classification: Optional[DataClassification] = None,
    action: Optional[RecommendedAction] = None,
    missing_data_only: bool = False
):
    """
    Runs the rules engine on ingested storage objects and returns generated recommendations with evidence snapshots.
    """
    engine = LifecycleRulesEngine(retention_rules=RETENTION_RULES_DB)
    objects_to_eval = list(OBJECTS_DB.values())

    if classification:
        objects_to_eval = [o for o in objects_to_eval if o.data_classification == classification]

    recommendations = engine.evaluate_batch(objects_to_eval)

    if action:
        recommendations = [r for r in recommendations if r.recommended_action == action]

    if missing_data_only:
        recommendations = [r for r in recommendations if r.confidence_tier == "LOW"]

    return recommendations


# --- STUBBED ENDPOINTS FOR PHASE 2 DEFERRAL ---

@app.post("/api/v1/recommendations/{object_id}/confirm", status_code=status.HTTP_501_NOT_IMPLEMENTED)
def confirm_recommendation(object_id: str):
    """
    Phase 2 Stub: Human confirmation of storage lifecycle action.
    """
    if object_id not in OBJECTS_DB:
        raise HTTPException(status_code=404, detail=f"Storage object '{object_id}' not found")
    return {
        "status": "deferred",
        "message": "Human confirmation approval workflow is deferred to Phase 2."
    }


@app.post("/api/v1/recommendations/{object_id}/override", status_code=status.HTTP_501_NOT_IMPLEMENTED)
def override_recommendation(object_id: str, reason: Optional[str] = None):
    """
    Phase 2 Stub: Human override of recommendation with rationale logging.
    """
    if object_id not in OBJECTS_DB:
        raise HTTPException(status_code=404, detail=f"Storage object '{object_id}' not found")
    return {
        "status": "deferred",
        "message": "Human override workflow and audit logging are deferred to Phase 2."
    }
