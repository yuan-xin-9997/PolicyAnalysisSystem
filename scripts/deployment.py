"""Quiescent deployment preflight, online SQLite backup and configured migration."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import sqlite3
import tarfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from policy_analysis.core.settings import load_settings
from policy_analysis.main import _upgrade_database


def environment(root):
    result = {}
    path = root / "service.env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            name, value = line.removeprefix("export ").split("=", 1)
            values = shlex.split(value, comments=True)
            result[name.strip()] = " ".join(values)
    result.update(os.environ)
    return result


def preflight(root, settings):
    if not settings.ai.public_base_url:
        raise RuntimeError(
            "部署前必须配置 AI public_base_url（现有 HTTPS 站点），否则远程 OAuth 发现不可用。"
        )
    if not settings.database.path.exists():
        return {"first_deployment": True}
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S-%f")
    backup = root / "src/data/deployment-backups" / stamp
    backup.mkdir(parents=True, mode=0o700)
    with sqlite3.connect(settings.database.path) as source:
        for table in ("crawl_tasks", "analysis_tasks"):
            exists = source.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if (
                exists
                and source.execute(
                    f"SELECT count(*) FROM {table} WHERE status IN ('pending','running')"
                ).fetchone()[0]
            ):
                raise RuntimeError("存在未完成任务；本次部署停止，请待任务完成后重试。")
        with sqlite3.connect(backup / "app.sqlite3") as target:
            source.backup(target)
    for path in (root / "src/config/app.json", settings.auth.password_file, root / "service.env"):
        if path.exists():
            destination = backup / path.name
            shutil.copy2(path, destination)
            destination.chmod(0o600)
    (backup / "app.sqlite3").chmod(0o600)
    with tarfile.open(backup / "code.tar.gz", "w:gz") as archive:
        for relative in (
            "src/app/backend",
            "src/app/frontend/dist",
            "migrations",
            "scripts",
            "pyproject.toml",
        ):
            path = root / relative
            if path.exists():
                archive.add(
                    path,
                    arcname=relative,
                    filter=lambda member: None if "__pycache__" in member.name else member,
                )
    (backup / "code.tar.gz").chmod(0o600)
    return {"backup": str(backup), "database": str(settings.database.path), "active_tasks": 0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["preflight", "migrate"])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    settings = load_settings(root / "src/config/app.json", root, environment(root))
    if args.action == "preflight":
        print(json.dumps(preflight(root, settings)))
    else:
        _upgrade_database(settings.database.path)
        print(json.dumps({"migration": "head"}))


if __name__ == "__main__":
    main()
