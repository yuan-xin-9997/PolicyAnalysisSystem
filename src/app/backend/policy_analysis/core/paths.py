"""Project path helpers."""

from pathlib import Path


def resolve_project_path(project_root: Path, value: Path) -> Path:
    """Resolve a configured path relative to the project root."""
    return value if value.is_absolute() else (project_root / value).resolve()
