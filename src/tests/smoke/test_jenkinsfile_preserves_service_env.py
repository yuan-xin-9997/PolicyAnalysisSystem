from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_jenkinsfile_preserves_local_service_environment_file() -> None:
    jenkinsfile = (PROJECT_ROOT / "src/JenkinsConfig/Jenkinsfile").read_text(encoding="utf-8")

    assert "service.env" in jenkinsfile
    assert "--exclude 'service.env'" in jenkinsfile
    assert ". ./service.env" in jenkinsfile
    assert "set +x" in jenkinsfile


def test_jenkinsfile_verifies_backend_and_frontend_before_stopping_service() -> None:
    jenkinsfile = (PROJECT_ROOT / "src/JenkinsConfig/Jenkinsfile").read_text(encoding="utf-8")

    verify_stage = jenkinsfile.index("stage('Verify')")
    stop_stage = jenkinsfile.index("stage('Stop current service')")
    assert verify_stage < stop_stage
    for command in (
        ".venv/bin/pytest -q",
        ".venv/bin/ruff check src/app/backend src/tests/backend",
        "npm --prefix src/app/frontend run type-check",
        "npm --prefix src/app/frontend run lint",
        "npm --prefix src/app/frontend run test -- --run",
        "npm --prefix src/app/frontend run build",
    ):
        assert command in jenkinsfile


def test_jenkinsfile_preserves_deployed_application_configuration() -> None:
    jenkinsfile = (PROJECT_ROOT / "src/JenkinsConfig/Jenkinsfile").read_text(encoding="utf-8")

    assert 'cp "${DEPLOY_DIR}/src/config/app.json" /tmp/policy-analysis-app.json' in jenkinsfile
    assert 'mv /tmp/policy-analysis-app.json "${DEPLOY_DIR}/src/config/app.json"' in jenkinsfile
