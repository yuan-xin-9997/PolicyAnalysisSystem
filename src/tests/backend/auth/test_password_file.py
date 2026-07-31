import os
import stat
from pathlib import Path

import pytest
from policy_analysis.auth.password_file import (
    PasswordEntry,
    parse_password_text,
    render_password_text,
    replace_password_file,
)


def test_parse_ignores_comments_and_preserves_valid_roles() -> None:
    text = "# comment\nadmin:admin123:admin\nreader:read123:user\n"

    assert parse_password_text(text) == [
        PasswordEntry("admin", "admin123", "admin"),
        PasswordEntry("reader", "read123", "user"),
    ]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("missing-fields\n", "格式无效"),
        (":password:user\n", "内容无效"),
        ("reader::user\n", "内容无效"),
        ("reader:password:operator\n", "内容无效"),
        ("reader:first:user\nreader:second:user\n", "内容无效"),
    ],
)
def test_parse_rejects_invalid_or_duplicate_entries(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_password_text(text)


def test_render_round_trips_entries_without_exposing_old_content() -> None:
    entries = [PasswordEntry("reader", "new-password", "user")]

    rendered = render_password_text(entries)

    assert parse_password_text(rendered) == entries
    assert rendered.startswith("# 格式: username:password:role")
    assert "old-password" not in rendered


def test_replace_writes_private_file_and_uses_same_directory_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "credentials" / "password.txt"
    replacement_calls: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def recording_replace(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    ) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        replacement_calls.append((source_path, destination_path))
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", recording_replace)

    replace_password_file(path, [PasswordEntry("reader", "safe-test-password", "user")])

    assert parse_password_text(path.read_text(encoding="utf-8")) == [
        PasswordEntry("reader", "safe-test-password", "user")
    ]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert replacement_calls == [(replacement_calls[0][0], path)]
    assert replacement_calls[0][0].parent == path.parent
    assert not list(path.parent.glob(f".{path.name}.*"))


def test_replace_keeps_existing_file_and_cleans_temporary_file_when_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "password.txt"
    original = "reader:previous-test-password:user\n"
    path.write_text(original, encoding="utf-8")

    def failing_fsync(_descriptor: int) -> None:
        raise OSError("simulated fsync failure")

    monkeypatch.setattr(os, "fsync", failing_fsync)

    with pytest.raises(OSError, match="simulated fsync failure"):
        replace_password_file(path, [PasswordEntry("reader", "updated-test-password", "user")])

    assert path.read_text(encoding="utf-8") == original
    assert not list(path.parent.glob(f".{path.name}.*"))


def test_replace_keeps_existing_file_and_cleans_temporary_file_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "password.txt"
    original = "reader:previous-test-password:user\n"
    path.write_text(original, encoding="utf-8")

    def failing_replace(
        _source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        _destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    ) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", failing_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        replace_password_file(path, [PasswordEntry("reader", "updated-test-password", "user")])

    assert path.read_text(encoding="utf-8") == original
    assert not list(path.parent.glob(f".{path.name}.*"))
