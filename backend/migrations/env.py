from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from backend.app.database import Base
import backend.app.models  # noqa: F401  Register every model with Base.metadata.


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Command-line and deployment configuration take precedence over alembic.ini.
database_url = os.getenv("DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)

target_metadata = Base.metadata


def include_object(object_, name, type_, reflected, compare_to):
    """LangGraph owns checkpoint tables via PostgresSaver.setup(), not Alembic."""
    table_name = name if type_ == "table" else getattr(getattr(object_, "table", None), "name", "")
    if table_name.startswith("checkpoint"):
        return False
    # M4 creates PostgreSQL-only expression/HNSW indexes manually. SQLAlchemy
    # metadata cannot portably model their operator classes in SQLite tests;
    # revision 20260914_0011 remains their sole schema owner.
    if type_ == "index" and name in {"ix_knowledge_chunks_search", "ix_knowledge_chunks_embedding_hnsw"}:
        return False
    return True


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True, include_object=include_object)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
