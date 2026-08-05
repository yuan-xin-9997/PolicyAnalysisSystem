from __future__ import annotations

import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_lifecycle_scripts_are_present_and_portable() -> None:
    expected = ["start.sh", "stop.sh", "status.sh", "start.ps1", "stop.ps1", "status.ps1"]

    for name in expected:
        script = PROJECT_ROOT / name
        assert script.is_file(), f"{name} should exist at repository root"
        content = script.read_text(encoding="utf-8")
        assert "/Users/" not in content
        assert "/opt/PolicyAnalysisSystem" not in content


def test_shell_lifecycle_scripts_have_valid_syntax() -> None:
    for name in ["start.sh", "stop.sh", "status.sh"]:
        subprocess.run(["bash", "-n", str(PROJECT_ROOT / name)], check=True)
