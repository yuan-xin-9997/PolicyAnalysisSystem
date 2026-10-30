from pathlib import Path

import pytest
from policy_analysis.auth.initialize import initialize_admin


def test_initial_admin_has_unique_random_password(tmp_path: Path):
    first = tmp_path / "first/password.txt"
    second = tmp_path / "second/password.txt"
    initialize_admin(first, {})
    initialize_admin(second, {})
    password = first.read_text().strip().split(":")[1]
    assert len(password) >= 24
    assert password != "admin123"
    assert first.read_text() != second.read_text()


def test_explicit_initial_password_and_preserve_existing_accounts(tmp_path: Path):
    target = tmp_path / "password.txt"
    initialize_admin(target, {"POLICY_ANALYSIS_INITIAL_ADMIN_PASSWORD": "test-explicit-password"})
    assert target.read_text() == "admin:test-explicit-password:admin\n"
    original = target.read_bytes()
    initialize_admin(target, {"POLICY_ANALYSIS_INITIAL_ADMIN_PASSWORD": "different-password"})
    assert target.read_bytes() == original


@pytest.mark.parametrize(
    "password",
    [
        "",
        "bad:delimiter",
        "bad\nline",
        "bad\rline",
        " leading-space",
        "trailing-space ",
        "control\x00character",
    ],
)
def test_invalid_explicit_password_leaves_no_file(tmp_path: Path, password: str):
    target = tmp_path / "password.txt"
    with pytest.raises(ValueError):
        initialize_admin(target, {"POLICY_ANALYSIS_INITIAL_ADMIN_PASSWORD": password})
    assert not target.exists()


def test_bootstrap_password_is_not_application_configuration(tmp_path: Path):
    from policy_analysis.core.settings import load_settings_snapshot

    config = tmp_path / "app.json"
    config.write_text("{}", encoding="utf-8")
    snapshot = load_settings_snapshot(
        config, tmp_path, {"POLICY_ANALYSIS_INITIAL_ADMIN_PASSWORD": "test-password"}
    )
    assert "initial_admin_password" not in snapshot.settings.model_dump()
    assert "initial_admin_password" not in snapshot.sources


def test_cli_uses_service_environment_and_preserves_existing_accounts(tmp_path, monkeypatch, capsys):
    from scripts.initialize_admin import main

    monkeypatch.delenv("POLICY_ANALYSIS_INITIAL_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("POLICY_ANALYSIS_AUTH__PASSWORD_FILE", raising=False)
    (tmp_path / "service.env").write_text(
        'POLICY_ANALYSIS_AUTH__PASSWORD_FILE="private/accounts.txt"\n'
        'POLICY_ANALYSIS_INITIAL_ADMIN_PASSWORD="service-test-password"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("sys.argv", ["initialize_admin", "--root", str(tmp_path)])
    main()
    target = tmp_path / "private/accounts.txt"
    assert target.read_text() == "admin:service-test-password:admin\n"
    target.write_text("existing:existing-test-password:admin\n", encoding="utf-8")
    main()
    assert target.read_text() == "existing:existing-test-password:admin\n"
    assert "service-test-password" not in capsys.readouterr().out


def test_cli_process_environment_overrides_service_environment(tmp_path, monkeypatch):
    from scripts.initialize_admin import main

    (tmp_path / "service.env").write_text(
        'POLICY_ANALYSIS_AUTH__PASSWORD_FILE="service/accounts.txt"\n'
        'POLICY_ANALYSIS_INITIAL_ADMIN_PASSWORD="service-test-password"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("POLICY_ANALYSIS_AUTH__PASSWORD_FILE", "process/accounts.txt")
    monkeypatch.setenv("POLICY_ANALYSIS_INITIAL_ADMIN_PASSWORD", "process-test-password")
    monkeypatch.setattr("sys.argv", ["initialize_admin", "--root", str(tmp_path)])
    main()
    assert (tmp_path / "process/accounts.txt").read_text() == "admin:process-test-password:admin\n"
    assert not (tmp_path / "service/accounts.txt").exists()
