from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_jenkinsfile_preserves_local_service_environment_file() -> None:
    jenkinsfile = (PROJECT_ROOT / "src/JenkinsConfig/Jenkinsfile").read_text(encoding="utf-8")

    assert "service.env" in jenkinsfile
    assert "--exclude 'service.env'" in jenkinsfile
    assert ". ./service.env" in jenkinsfile
    assert "set +x" in jenkinsfile
