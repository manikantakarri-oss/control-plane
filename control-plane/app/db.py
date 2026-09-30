from __future__ import annotations

import threading
import time
from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings, get_settings


class Base(DeclarativeBase):
    pass


class _LakebaseConnector:
    """Connects to a Lakebase (Databricks Postgres) instance as the current
    Databricks identity - the app's service principal when deployed, the
    developer's CLI login locally.

    Lakebase passwords are OAuth tokens valid for about an hour. A token is only
    checked when a connection opens, so one is cached and reused for new
    connections until shortly before it expires, and pooled connections are
    recycled well inside that window.
    """

    REFRESH_BEFORE_EXPIRY = 600  # seconds
    TOKEN_LIFETIME = 3600

    def __init__(self, instance: str, database: str):
        from databricks.sdk import WorkspaceClient

        self._w = WorkspaceClient()
        self._instance = instance
        self._database = database
        self._lock = threading.Lock()
        self._token: tuple[str, float] | None = None
        self._host = self._w.database.get_database_instance(name=instance).read_write_dns
        self._user = self._w.current_user.me().user_name

    def _password(self) -> str:
        with self._lock:
            if self._token is None or self._token[1] - time.time() < self.REFRESH_BEFORE_EXPIRY:
                cred = self._w.database.generate_database_credential(instance_names=[self._instance])
                self._token = (cred.token, time.time() + self.TOKEN_LIFETIME)
            return self._token[0]

    def connect(self):
        import psycopg

        return psycopg.connect(
            host=self._host,
            port=5432,
            dbname=self._database,
            user=self._user,
            password=self._password(),
            sslmode="require",
        )


def _use_schema(engine, schema: str) -> None:
    """Point every new connection at `schema`. Set in autocommit so a later
    transaction rollback cannot undo it (plain SET is transactional)."""

    @event.listens_for(engine, "connect")
    def _set_search_path(dbapi_conn, _record):
        previous = dbapi_conn.autocommit
        dbapi_conn.autocommit = True
        try:
            dbapi_conn.execute(f'SET search_path TO "{schema}"')
        finally:
            dbapi_conn.autocommit = previous


def make_engine(cfg: Settings):
    if cfg.lakebase_instance:
        connector = _LakebaseConnector(cfg.lakebase_instance, cfg.lakebase_database)
        engine = create_engine(
            "postgresql+psycopg://",
            creator=connector.connect,
            pool_pre_ping=True,
            pool_recycle=1800,
            pool_size=5,
            max_overflow=5,
        )
    else:
        url = cfg.database_url
        if not url:
            raise RuntimeError("set DATABASE_URL (a Postgres URL) or LAKEBASE_INSTANCE")
        if url.startswith("sqlite"):
            # Tests use an in-memory database; StaticPool keeps every session on
            # the same connection so that database is actually shared.
            kwargs = {"connect_args": {"check_same_thread": False}}
            if ":memory:" in url:
                kwargs["poolclass"] = StaticPool
            return create_engine(url, **kwargs)
        engine = create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=10)
    if cfg.database_schema:
        _use_schema(engine, cfg.database_schema)
    return engine


engine = make_engine(get_settings())
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
