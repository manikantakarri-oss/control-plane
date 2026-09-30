"""Runtime configuration, read once from the environment.

Every setting has a safe default for local development except the admin API
key: in production (``ENVIRONMENT=production``) a missing key is a startup
error rather than an open API.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # No populate_by_name: that would also read the unprefixed GITHUB_* names.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = Field("development", description="development | production")
    # SQLAlchemy URL, e.g. postgresql+psycopg://user:pass@host:5432/db. Required
    # unless LAKEBASE_INSTANCE is set; docker-compose.yml / .env.example supply
    # one for local development.
    database_url: str = ""
    # When hosted as a Databricks App: the Lakebase instance to use instead of
    # DATABASE_URL. Credentials are short-lived OAuth tokens minted for the
    # app's own identity, so there is no database password to manage.
    lakebase_instance: str = ""
    lakebase_database: str = "databricks_postgres"
    # Postgres schema holding the Control Plane's tables; created by the first
    # migration if missing. Empty = the connection's default (usually public).
    # Needed on Lakebase, where the app may create schemas but not write to
    # public (Postgres 15+ default privileges).
    database_schema: str = Field("", pattern=r"^([a-z_][a-z0-9_]{0,62})?$")
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
    # the Control Plane only dispatches them. DEPLOY_GITHUB_TOKEN needs "Actions:
    # write" on DEPLOY_GITHUB_REPO and nothing else. Leave unset to disable the
    # deploy API. Prefixed because GitHub Actions itself sets GITHUB_REF,
    # GITHUB_WORKFLOW, GITHUB_TOKEN and GITHUB_API_URL with other meanings.
    github_repo: str = Field("", validation_alias="DEPLOY_GITHUB_REPO", description="owner/name")
    github_token: str = Field("", validation_alias="DEPLOY_GITHUB_TOKEN")
    github_workflow: str = Field("deploy-customer.yml", validation_alias="DEPLOY_GITHUB_WORKFLOW")
    github_ref: str = Field("main", validation_alias="DEPLOY_GITHUB_REF")
    github_api_url: str = Field("https://api.github.com", validation_alias="DEPLOY_GITHUB_API_URL")
    # A run still requested/running after this long is assumed lost and no
    # longer blocks a new deploy of the same deployment.
    deploy_run_timeout_seconds: int = 3600

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def deploys_enabled(self) -> bool:
        return bool(self.github_repo and self.github_token)

    @field_validator("github_token")
    @classmethod
    def _placeholder_means_unset(cls, v: str) -> str:
        # Databricks secrets cannot be empty, so a scope that has no token yet
        # holds the placeholder "unset".
        return "" if v.strip().lower() in ("", "unset") else v.strip()

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
