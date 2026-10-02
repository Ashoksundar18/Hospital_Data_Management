import pytest
from src.models.storage_object import StorageClass, CloudProvider, DataClassification
from src.models.recommendation import RecommendedAction, ApprovalStatus
from src.services.cost_service import (
    calculate_object_cost,
    generate_cost_report,
    S3_STORAGE_PRICING,
    BYTES_PER_GB,
    CostEvaluationItem,
    CostReport
)


def test_hand_computed_fixture_cost_report():
    """
    Verifies that before/after storage cost calculations match hand-computed
    exact values for a 4-object fixture dataset across different actions,
    storage classes, and governance/legal-hold states.
    """
    # 1 GB = 1024^3 bytes
    gb = BYTES_PER_GB

    # Hand-computed test fixtures:
    # Obj 1: 10 GB HOT -> TRANSITION COOL (executed)
    # Current: 10 * 0.023 = $0.23/mo
    # Projected: 10 * 0.0125 = $0.125/mo
    # Savings: $0.105/mo
    item1 = CostEvaluationItem(
        object_id="obj-c1",
        size_bytes=10 * gb,
        current_class=StorageClass.HOT,
        recommended_action=RecommendedAction.TRANSITION,
        target_storage_class=StorageClass.COOL,
        approval_status=ApprovalStatus.CONFIRMED,
        execution_status="executed",
        legal_hold=False
    )

    # Obj 2: 20 GB HOT -> DELETE (confirmed / pending execution)
    # Current: 20 * 0.023 = $0.46/mo
    # Projected: $0.00/mo
    # Savings: $0.46/mo
    item2 = CostEvaluationItem(
        object_id="obj-c2",
        size_bytes=20 * gb,
        current_class=StorageClass.HOT,
        recommended_action=RecommendedAction.DELETE,
        target_storage_class=None,
        approval_status=ApprovalStatus.CONFIRMED,
        execution_status="pending_execution",
        legal_hold=False
    )

    # Obj 3: 5 GB HOT -> TRANSITION ARCHIVE (legal hold = True -> blocked)
    # Current: 5 * 0.023 = $0.115/mo
    # Projected: 5 * 0.0036 = $0.018/mo
    # Blocked Savings: $0.097/mo
    item3 = CostEvaluationItem(
        object_id="obj-c3",
        size_bytes=5 * gb,
        current_class=StorageClass.HOT,
        recommended_action=RecommendedAction.TRANSITION,
        target_storage_class=StorageClass.ARCHIVE,
        approval_status=ApprovalStatus.PENDING,
        execution_status="blocked_by_legal_hold",
        legal_hold=True
    )

    # Obj 4: 10 GB COOL -> NO_ACTION (pending review)
    # Current: 10 * 0.0125 = $0.125/mo
    # Projected: 10 * 0.0125 = $0.125/mo
    # Savings: $0.00/mo
    item4 = CostEvaluationItem(
        object_id="obj-c4",
        size_bytes=10 * gb,
        current_class=StorageClass.COOL,
        recommended_action=RecommendedAction.NO_ACTION,
        target_storage_class=None,
        approval_status=ApprovalStatus.PENDING,
        execution_status="pending",
        legal_hold=False
    )

    items = [item1, item2, item3, item4]
    report = generate_cost_report(items)

    # Exact hand-calculated checks:
    # Total GB = 10 + 20 + 5 + 10 = 45 GB
    assert report.total_objects == 4
    assert pytest.approx(report.total_size_gb, rel=1e-5) == 45.0

    # Total baseline monthly cost = 0.23 + 0.46 + 0.115 + 0.125 = $0.93 / month
    assert pytest.approx(report.total_baseline_monthly_cost, rel=1e-5) == 0.93
    # Total baseline annual cost = 0.93 * 12 = $11.16 / year
    assert pytest.approx(report.total_baseline_annual_cost, rel=1e-5) == 11.16

    # Realized monthly savings (executed obj 1) = $0.105 / month
    assert pytest.approx(report.realized_monthly_savings, rel=1e-5) == 0.105
    assert pytest.approx(report.realized_annual_savings, rel=1e-5) == 0.105 * 12

    # Approved/queued monthly savings (confirmed obj 2) = $0.46 / month
    assert pytest.approx(report.approved_queued_monthly_savings, rel=1e-5) == 0.46

    # Blocked monthly savings by legal hold (obj 3) = $0.097 / month
    assert pytest.approx(report.blocked_by_legal_hold_monthly_savings, rel=1e-5) == 0.097

    # Total realizable monthly savings (executed + approved = 0.105 + 0.46) = $0.565 / month
    assert pytest.approx(report.total_realizable_monthly_savings, rel=1e-5) == 0.565
    assert pytest.approx(report.total_realizable_annual_savings, rel=1e-5) == 0.565 * 12


def test_calculate_single_object_cost():
    """
    Verifies single object monthly and annual before/after cost calculations.
    """
    # 100 GB in HOT tier transitioning to DEEP_ARCHIVE
    # HOT: 100 * 0.023 = $2.30/mo
    # DEEP_ARCHIVE: 100 * 0.00099 = $0.099/mo
    # Monthly Savings: 2.30 - 0.099 = $2.201/mo
    size_bytes = 100 * BYTES_PER_GB
    calc = calculate_object_cost(
        size_bytes=size_bytes,
        current_class=StorageClass.HOT,
        recommended_action=RecommendedAction.TRANSITION,
        target_storage_class=StorageClass.DEEP_ARCHIVE,
        legal_hold=False
    )

    assert pytest.approx(calc.current_monthly_cost, rel=1e-4) == 2.30
    assert pytest.approx(calc.projected_monthly_cost, rel=1e-4) == 0.099
    assert pytest.approx(calc.monthly_savings, rel=1e-4) == 2.201
    assert pytest.approx(calc.annual_savings, rel=1e-4) == 2.201 * 12
    assert calc.is_blocked_by_legal_hold is False


def test_cost_report_api_endpoint(monkeypatch):
    """
    Verifies that GET /api/v1/cost/report returns HTTP 200 with structured cost report data.
    """
    from fastapi.testclient import TestClient
    from src.api.main import app

    monkeypatch.setenv("API_KEY_VIEWER", "viewer-key-cost")
    headers = {"X-API-Key": "viewer-key-cost"}

    with TestClient(app) as client:
        res = client.get("/api/v1/cost/report?include_itemized=true", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert "total_objects" in data
        assert "total_size_gb" in data
        assert "total_baseline_monthly_cost" in data
        assert "total_baseline_annual_cost" in data
        assert "breakdown_by_status" in data
        assert "breakdown_by_action" in data
        assert "itemized_costs" in data
        assert isinstance(data["itemized_costs"], list)

