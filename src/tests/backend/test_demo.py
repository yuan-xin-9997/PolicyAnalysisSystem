from pathlib import Path

from policy_analysis.core.database import build_engine, session_factory
from policy_analysis.demo import import_demo
from policy_analysis.policies.models import Policy
from policy_analysis.policies.repository import PolicyRepository
from policy_analysis.policies.schemas import PolicyQuery
from sqlalchemy import func, select


def test_demo_is_isolated_searchable_and_idempotent(tmp_path: Path) -> None:
    assert import_demo(tmp_path) == 3
    password = (tmp_path / "password.txt").read_bytes()
    assert b"admin123" not in password
    assert import_demo(tmp_path) == 0
    assert (tmp_path / "password.txt").read_bytes() == password
    engine = build_engine(tmp_path / "app.sqlite3")
    try:
        with session_factory(engine)() as session:
            assert session.scalar(select(func.count(Policy.id))) == 3
            records, total = PolicyRepository(session).search(PolicyQuery(keyword="教育"))
            assert total >= 1
            assert records[0].policy.content_text
    finally:
        engine.dispose()


def test_demo_real_application_flow(tmp_path: Path) -> None:
    import time

    from fastapi.testclient import TestClient
    from policy_analysis.main import create_app

    import_demo(tmp_path)
    password = (tmp_path / "password.txt").read_text().strip().split(":")[1]
    app = create_app(
        environment={
            "POLICY_ANALYSIS_DATABASE__PATH": str(tmp_path / "app.sqlite3"),
            "POLICY_ANALYSIS_AUTH__PASSWORD_FILE": str(tmp_path / "password.txt"),
        }
    )
    with TestClient(app) as client:
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": password})
        assert login.status_code == 200
        headers = {"X-CSRF-Token": login.json()["csrf_token"]}
        response = client.get("/api/v1/policies", params={"full_text": "教育"})
        assert response.status_code == 200
        ids = [item["id"] for item in response.json()["items"]]
        assert len(ids) == 2
        created = client.post("/api/v1/analysis/comparison-tasks", json={"policy_ids": ids}, headers=headers)
        assert created.status_code == 200
        task_id = created.json()["task_id"]
        for _ in range(100):
            status = client.get(f"/api/v1/analysis/tasks/{task_id}").json()["status"]
            if status in {"succeeded", "failed"}:
                break
            time.sleep(0.05)
        assert status == "succeeded"
        report = client.get(f"/api/v1/analysis/tasks/{task_id}/comparison-report")
        assert report.status_code == 200
        assert 0 < report.json()["pair_differences"][0]["similarity"] < 1
        assert client.post("/api/v1/auth/logout").status_code == 403
