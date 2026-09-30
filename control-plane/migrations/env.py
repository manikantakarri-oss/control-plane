from alembic import context
from sqlalchemy import create_engine, pool, text

from app import models  # noqa: F401 - registers tables on Base.metadata
from app.config import get_settings
from app.db import Base, engine

config = context.config
target_metadata = Base.metadata
# Tables and alembic's own version table live in this schema when set; the
# app's engine already points each connection's search_path at it.
SCHEMA = get_settings().database_schema or None


def _connectable():
    # An explicitly passed URL (tests) wins; otherwise use the app's own engine,
    # which knows how to authenticate to Lakebase.
    url = config.get_main_option("sqlalchemy.url")
    return create_engine(url, poolclass=pool.NullPool) if url else engine


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url") or engine.url.render_as_string(hide_password=False)
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, version_table_schema=SCHEMA)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = _connectable()
    use_schema = SCHEMA and connectable.dialect.name == "postgresql"
    with connectable.connect() as connection:
        if use_schema:
            connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))
            connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            version_table_schema=SCHEMA if use_schema else None,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
