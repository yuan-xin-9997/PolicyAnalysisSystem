import sqlite3

import pytest
from alembic import command
from alembic.config import Config
from policy_analysis.core.settings import AppSettings
from sqlalchemy import create_engine, inspect

from scripts.deployment import preflight


def test_ai_migration_upgrade_downgrade_preserves_business(tmp_path):
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    path = tmp_path / "migration.sqlite3"
    config = Config(str(root / "alembic.ini"))
    config.attributes["database_path"] = path
    command.upgrade(config, "0007")
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO policy_categories(code,name,is_active) VALUES ('test','保留数据',1)"
        )
    command.upgrade(config, "head")
    assert "ai_credentials" in inspect(engine).get_table_names()
    command.downgrade(config, "0007")
    assert "ai_credentials" not in inspect(engine).get_table_names()
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT name FROM policy_categories").scalar_one() == "保留数据"
    command.upgrade(config, "head")
    engine.dispose()


def test_deployment_checks_tasks_and_backs_up(tmp_path):
    root = tmp_path / "deploy"
    root.mkdir()
    database = root / "app.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE analysis_tasks (status TEXT)")
        db.execute("INSERT INTO analysis_tasks VALUES ('running')")
    settings = AppSettings.model_validate(
        {"database": {"path": database}, "ai": {"public_base_url": "https://policy.example.org"}}
    )
    with pytest.raises(RuntimeError, match="未完成任务"):
        preflight(root, settings)
    with sqlite3.connect(database) as db:
        db.execute("UPDATE analysis_tasks SET status='succeeded'")
    report = preflight(root, settings)
    with sqlite3.connect(report["backup"] + "/app.sqlite3") as db:
        assert db.execute("SELECT status FROM analysis_tasks").fetchone()[0] == "succeeded"
