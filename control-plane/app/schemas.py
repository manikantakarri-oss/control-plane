from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator


class HealthStatus(StrEnum):
    healthy = "healthy"
    degraded = "degraded"
    unhealthy = "unhealthy"


class Connectivity(StrEnum):
    never_seen = "never_seen"
    online = "online"
    stale = "stale"


class DeployMode(StrEnum):
    # onboard: issue fresh Control Plane credentials into the customer's secret
    # scope, then deploy. upgrade: deploy code only.
    onboard = "onboard"
    upgrade = "upgrade"


class DeployRunStatus(StrEnum):
    requested = "requested"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


# GitHub Environment names: keep to a conservative charset, since the value is
# passed through to a workflow.
GITHUB_ENV_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$"


class DeploymentRegisterRequest(BaseModel):
    customer_name: str = Field(min_length=1, max_length=200)
    workspace_host: str = Field(max_length=255)
    app_name: str = Field("agent-portal", pattern=r"^[a-z0-9][a-z0-9-]{1,98}[a-z0-9]$")
    github_environment: str | None = Field(None, pattern=GITHUB_ENV_PATTERN)

    @field_validator("customer_name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("customer_name must not be blank")
        return v

    @field_validator("workspace_host")
    @classmethod
    def _normalise_host(cls, v: str) -> str:
        v = v.strip()
        if "://" not in v:
            v = "https://" + v
        parsed = urlparse(v)
        host = parsed.hostname or ""
        if parsed.scheme != "https" or "." not in host or parsed.path not in ("", "/"):
            raise ValueError("workspace_host must be an https workspace URL, e.g. https://dbc-123.cloud.databricks.com")
        return f"https://{host}"


class DeploymentResponse(BaseModel):
    id: str
    customer_id: str
    customer_name: str
    workspace_host: str
    app_name: str
    status: str
    connectivity: Connectivity
    last_heartbeat_at: datetime | None = None
    last_health_status: HealthStatus | None = None
    app_version: str | None = None
    github_environment: str | None = None
    created_at: datetime
    updated_at: datetime


class DeploymentUpdate(BaseModel):
    github_environment: str | None = Field(None, pattern=GITHUB_ENV_PATTERN)


class DeploymentCredentials(DeploymentResponse):
    """Returned only by register/rotate. The token is never retrievable again."""

    deployment_token: str


class DeploymentList(BaseModel):
    items: list[DeploymentResponse]
    total: int
    limit: int
    offset: int


class CustomerResponse(BaseModel):
    id: str
    name: str
    created_at: datetime
    deployment_count: int


class HeartbeatRequest(BaseModel):
    health_status: HealthStatus = HealthStatus.healthy
    app_version: str | None = Field(None, max_length=64)


class HeartbeatAck(BaseModel):
    received_at: datetime


class DeployRequest(BaseModel):
    mode: DeployMode = DeployMode.upgrade
    # Git ref to deploy; defaults to GITHUB_REF (usually main). A tag pins a release.
    ref: str | None = Field(None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")
    # Stop + start the app afterwards; needed after a user_api_scopes change.
    restart: bool = False


class DeployRunResponse(BaseModel):
    id: str
    deployment_id: str
    mode: DeployMode
    ref: str
    status: DeployRunStatus
    run_url: str | None = None
    detail: str | None = None
    requested_at: datetime
    updated_at: datetime


class DeployRunStatusUpdate(BaseModel):
    """Sent by the deploy workflow as it progresses."""

    status: DeployRunStatus
    run_url: str | None = Field(None, max_length=500, pattern=r"^https://")
    detail: str | None = Field(None, max_length=500)
