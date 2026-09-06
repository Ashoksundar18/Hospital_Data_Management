import pytest
from fastapi.testclient import TestClient
from src.api.main import app


def test_get_objects_endpoint():
    with TestClient(app) as client:
        response = client.get("/api/v1/objects")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0


def test_get_recommendations_endpoint():
    with TestClient(app) as client:
        response = client.get("/api/v1/recommendations")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0

        first = data[0]
        assert "object_id" in first
        assert "recommended_action" in first
        assert "triggering_rules" in first
        assert "evidence_snapshot" in first
        assert "confidence_tier" in first
        assert "reasoning_summary" in first


def test_post_object_endpoint():
    with TestClient(app) as client:
        new_obj = {
            "id": "obj-test-ingest-999",
            "bucket_or_account": "hospital-test-bucket",
            "cloud_provider": "AWS",
            "data_classification": "APP_LOG",
            "current_storage_class": "HOT",
            "size_bytes": 100000,
            "object_age_days": 10,
            "last_access_days_ago": 2,
            "access_frequency_30d": 15,
            "restore_event_history": [],
            "retention_rule_id": "rule-app-log-retention",
            "legal_hold": False
        }

        response = client.post("/api/v1/objects", json=new_obj)
        assert response.status_code == 201
        assert response.json()["object_id"] == "obj-test-ingest-999"


def test_stubbed_endpoints():
    with TestClient(app) as client:
        res_confirm = client.post("/api/v1/recommendations/obj-test-ingest-999/confirm")
        assert res_confirm.status_code == 501

        res_override = client.post("/api/v1/recommendations/obj-test-ingest-999/override")
        assert res_override.status_code == 501
