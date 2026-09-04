"""Alembic environment.

The database URL comes from ``DATABASE_URL`` in the environment, never from
alembic.ini: the ini is committed and the URL carries a password.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Importing the package registers every model on Base.metadata; autogenerate
# compares against exactly this.
from archguard.db import Base
from archguard.db.session import _normalise, database_url

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

config.set_main_option("sqlalchemy.url", _normalise(database_url()))


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        # Serialise concurrent migrators. Both containers run the entrypoint,
        # which runs `alembic upgrade head`, and on a first deploy against an
        # empty database they compute the same plan at the same time -- the
        # loser then fails with "relation already exists" and exits non-zero,
        # which is a failed release on Render and a restart loop on Railway.
        #
        # Alembic takes no such lock itself, despite the entrypoint's comment
        # saying it does. A transaction-scoped advisory lock is released when
        # this transaction ends, however it ends, so a crashed migrator cannot
        # leave the next deploy blocked. The constant is arbitrary but must be
        # stable: it is the identity of "the ArchGuard schema".
        if context.get_bind().dialect.name == "postgresql":
            context.get_bind().exec_driver_sql(
                "SELECT pg_advisory_xact_lock(6845127001)"
            )
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Without these, a changed column type or default is silently ignored by
        # autogenerate, and the migration that should have caught it is empty.
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        # Serialise concurrent migrators. Both containers run the entrypoint,
        # which runs `alembic upgrade head`, and on a first deploy against an
        # empty database they compute the same plan at the same time -- the
        # loser then fails with "relation already exists" and exits non-zero,
        # which is a failed release on Render and a restart loop on Railway.
        #
        # Alembic takes no such lock itself, despite the entrypoint's comment
        # saying it does. A transaction-scoped advisory lock is released when
        # this transaction ends, however it ends, so a crashed migrator cannot
        # leave the next deploy blocked. The constant is arbitrary but must be
        # stable: it is the identity of "the ArchGuard schema".
        if context.get_bind().dialect.name == "postgresql":
            context.get_bind().exec_driver_sql(
                "SELECT pg_advisory_xact_lock(6845127001)"
            )
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
