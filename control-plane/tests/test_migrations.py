"""The Alembic history must produce exactly the schema the models describe,
or production (which only ever runs migrations) drifts from tests (which use
create_all)."""

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from app.db import Base


def test_migrations_match_models(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    engine = create_engine(url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == []

    command.downgrade(cfg, "base")


def test_production_refuses_insecure_config(monkeypatch):
    import pytest
    from pydantic import ValidationError

    from app.config import Settings

    with pytest.raises(ValidationError):
        Settings(environment="production", admin_api_key="short", database_url="postgresql://x")
    with pytest.raises(ValidationError):
        Settings(environment="production", admin_api_key="k" * 40, database_url="sqlite:///x.db")
    Settings(environment="production", admin_api_key="k" * 40, database_url="postgresql://x")


def test_auto_migrate_on_startup(tmp_path, monkeypatch):
    """Databricks Apps path: the app migrates its own database at startup."""
    from alembic.config import Config as AlembicConfig

    from app import main

    url = f"sqlite:///{tmp_path / 'boot.db'}"
    real_config = AlembicConfig

    def config_with_url(*args, **kwargs):
        cfg = real_config(*args, **kwargs)
        cfg.set_main_option("sqlalchemy.url", url)
        return cfg

    monkeypatch.setattr("alembic.config.Config", config_with_url)
    main._migrate()

    engine = create_engine(url)
    with engine.connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []
    engine.dispose()


def test_unset_github_token_placeholder_disables_deploys():
    from app.config import Settings

    assert not Settings(DEPLOY_GITHUB_REPO="o/r", DEPLOY_GITHUB_TOKEN="unset").deploys_enabled
    assert Settings(DEPLOY_GITHUB_REPO="o/r", DEPLOY_GITHUB_TOKEN="real").deploys_enabled
