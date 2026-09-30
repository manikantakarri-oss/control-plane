"""Runtime configuration, read once from the environment.

Every setting has a safe default for local development except the admin API
key: in production (``ENVIRONMENT=production``) a missing key is a startup
error rather than an open API.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = Field("development", description="development | production")
    database_url: str = "postgresql+psycopg://controlplane:controlplane@localhost:5432/controlplane"
    # When hosted as a Databricks App: the Lakebase instance to use instead of
    # DATABASE_URL. Credentials are short-lived OAuth tokens minted for the
    # app's own identity, so there is no database password to manage.
    lakebase_instance: str = ""
    lakebase_database: str = "databricks_postgres"
    # Apply Alembic migrations when the app starts (Databricks Apps has no
    # separate release step). The container entrypoint migrates on its own.
    auto_migrate: bool = False
    # Provider-staff credential for the management API. Compared in constant
    # time; rotate by redeploying with a new value.
    admin_api_key: str = ""
    # A deployment that has not called in for this long is reported as stale.
    # Silence is the only unreachability signal: the Control Plane never calls
    # into a customer environment.
    heartbeat_stale_seconds: int = 300
    log_level: str = "INFO"

    # Deploys are executed by GitHub Actions (.github/workflows/deploy-customer.yml);
    # the Control Plane only dispatches them. GITHUB_TOKEN needs "Actions: write"
    # on GITHUB_REPO and nothing else. Leave unset to disable the deploy API.
    github_repo: str = Field("", description="owner/name")
    github_token: str = ""
    github_workflow: str = "deploy-customer.yml"
    github_ref: str = "main"
    github_api_url: str = "https://api.github.com"
    # A run still requested/running after this long is assumed lost and no
    # longer blocks a new deploy of the same deployment.
    deploy_run_timeout_seconds: int = 3600

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def deploys_enabled(self) -> bool:
        return bool(self.github_repo and self.github_token)

    @model_validator(mode="after")
    def _require_secrets_in_production(self) -> Settings:
        if self.is_production:
            if len(self.admin_api_key) < 32:
                raise ValueError("ADMIN_API_KEY must be set (>= 32 chars) in production")
            if not self.lakebase_instance and self.database_url.startswith("sqlite"):
                raise ValueError("SQLite is not supported in production; set DATABASE_URL or LAKEBASE_INSTANCE")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
