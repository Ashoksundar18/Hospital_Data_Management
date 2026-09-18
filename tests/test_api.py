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


def test_phase2_governance_endpoints():
    with TestClient(app) as client:
        # Ingest test object
        client.post("/api/v1/objects", json={
            "id": "obj-api-gov-test",
            "bucket_or_account": "b",
            "cloud_provider": "AWS",
            "data_classification": "BACKUP",
            "current_storage_class": "HOT",
            "size_bytes": 1000,
            "object_age_days": 10,
            "legal_hold": False
        })
        client.get("/api/v1/recommendations")

        # Confirm recommendation endpoint (Phase 2 real implementation)
        res_confirm = client.post(
            "/api/v1/recommendations/obj-api-gov-test/confirm",
            json={"reviewer_id": "usr-test"}
        )
        assert res_confirm.status_code == 200
        assert res_confirm.json()["approval_status"] == "confirmed"

        # Override recommendation endpoint (Phase 2 real implementation)
        res_override = client.post(
            "/api/v1/recommendations/obj-api-gov-test/override",
            json={"reviewer_id": "usr-test", "override_reason": "CUSTOM_SLA"}
        )
        assert res_override.status_code == 200
        assert res_override.json()["approval_status"] == "overridden"


def test_db_info_endpoint_allowed_keys_only():
    with TestClient(app) as client:
        response = client.get("/api/v1/db-info")
        assert response.status_code == 200
        data = response.json()
        assert set(data.keys()) == {"dialect", "driver", "url_scheme"}
        assert data["dialect"] in ["sqlite", "postgresql"]


