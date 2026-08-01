from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from policy_analysis.auth import models as auth_models
from policy_analysis.core.database import Base, build_engine
from policy_analysis.core.settings import load_settings
from policy_analysis.policies import models as policy_models
from policy_analysis.sources import models as source_models
from policy_analysis.tasks import models as task_models

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

del auth_models, policy_models, source_models, task_models
target_metadata = Base.metadata
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _database_path() -> Path:
    settings = load_settings(
        PROJECT_ROOT / "src/config/app.json",
        PROJECT_ROOT,
        os.environ,
    )
    return settings.database.path


def run_migrations_offline() -> None:
    database_path = _database_path()
    context.configure(
        url=f"sqlite+pysqlite:///{database_path}",
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = build_engine(_database_path())
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)

            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
