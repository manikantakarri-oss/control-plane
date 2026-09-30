from alembic import context
from sqlalchemy import create_engine, pool

from app import models  # noqa: F401 - registers tables on Base.metadata
from app.db import Base, engine

config = context.config
target_metadata = Base.metadata


def _connectable():
    # An explicitly passed URL (tests) wins; otherwise use the app's own engine,
    # which knows how to authenticate to Lakebase.
    url = config.get_main_option("sqlalchemy.url")
    return create_engine(url, poolclass=pool.NullPool) if url else engine


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url") or engine.url.render_as_string(hide_password=False)
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    with _connectable().connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
