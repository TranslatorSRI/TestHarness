"""Alembic environment for the Information Radiator."""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from radiator.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def database_url() -> str:
    url = config.attributes.get("database_url") or os.getenv("RADIATOR_DATABASE_URL")
    if not url:
        raise RuntimeError("RADIATOR_DATABASE_URL must be set")
    return url


def run_migrations_offline():
    context.configure(
        url=database_url(),
        target_metadata=Base.metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    engine = create_engine(database_url())
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=Base.metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
