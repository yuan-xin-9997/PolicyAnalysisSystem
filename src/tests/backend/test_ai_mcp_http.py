import asyncio
import json
import socket
import threading
import time
from datetime import UTC, datetime

import httpx
import uvicorn
from policy_analysis.main import create_app
from policy_analysis.policies.models import Policy
from policy_analysis.sources.models import PolicyCategory, Source
from sqlalchemy import select


def test_real_sdk_oauth_tools_and_async_task(tmp_path, monkeypatch):
    # Local SDK traffic must stay offline even when the host exports a proxy.
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    # A real TCP listener, isolated migrated database and official SDK client.
    from scripts.verify_ai_access import verify

    password = tmp_path / "src/data/password.txt"
    password.parent.mkdir(parents=True)
    password.write_text("admin:admin123:admin\n", encoding="utf-8")
    password.chmod(0o600)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    config = tmp_path / "src/config/app.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"ai": {"public_base_url": base}}), encoding="utf-8")
    app = create_app(project_root=tmp_path, environment={})
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        for _ in range(200):
            if server.started:
                break
            time.sleep(0.05)
        assert server.started
        with app.state.database_sessions.begin() as db:
            source = db.scalar(select(Source))
            category = db.scalar(select(PolicyCategory))
            timestamp = datetime.now(UTC)
            db.add(
                Policy(
                    source_id=source.id,
                    category_id=category.id,
                    title="AI 接入验收政策",
                    canonical_url="https://example.org/ai-test",
                    publisher="验收",
                    published_at=timestamp,
                    content_text="推动经济发展，促进科技创新。科技创新促进经济发展。",
                    content_hash="a" * 64,
                    webfetch_artifact_id="test",
                    first_crawled_at=timestamp,
                    last_crawled_at=timestamp,
                )
            )
        result = asyncio.run(verify(base, "admin", "admin123"))
        assert result["task_status"] == "succeeded"
        assert result["tools"] >= 20
        with httpx.Client(base_url=base) as client:
            assert client.post("/mcp/", json={}).status_code == 401
    finally:
        server.should_exit = True
        thread.join(timeout=20)
        sock.close()
    assert not thread.is_alive()
    # A new application instance must resume queued work, fail interrupted work, and retain results.
    from fastapi.testclient import TestClient
    from policy_analysis.analysis.repository import AnalysisRepository
    from policy_analysis.analysis.service import AnalysisService
    from policy_analysis.core.database import build_engine, session_factory

    engine = build_engine(tmp_path / "src/data/app.sqlite3")
    sessions = session_factory(engine)
    service = AnalysisService(sessions)
    interrupted = service.create_task([1], requested_by=None)
    assert AnalysisRepository(sessions).claim_next(datetime.now(UTC)) == interrupted.task_id
    queued = service.create_task([1], requested_by=None)
    restarted = create_app(project_root=tmp_path, environment={})
    try:
        with TestClient(restarted) as client:
            login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
            assert login.status_code == 200
            old = client.get(f"/api/v1/analysis/tasks/{result['task_id']}")
            assert old.json()["status"] == "succeeded"
            failed = client.get(f"/api/v1/analysis/tasks/{interrupted.task_id}")
            assert failed.json()["status"] == "failed"
            assert failed.json()["error_summary"]
            for _ in range(100):
                resumed = client.get(f"/api/v1/analysis/tasks/{queued.task_id}")
                if resumed.json()["status"] == "succeeded":
                    break
                time.sleep(0.05)
            assert resumed.json()["status"] == "succeeded"
    finally:
        engine.dispose()
