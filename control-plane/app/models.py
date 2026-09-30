"""Control Plane tables. Metadata only - never customer business data."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    deployments: Mapped[list[Deployment]] = relationship(back_populates="customer")


class Deployment(Base):
    """One Data Plane (Agent Portal app) inside one customer workspace."""

    __tablename__ = "deployments"
    __table_args__ = (UniqueConstraint("workspace_host", "app_name", name="uq_deployment_workspace_app"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    workspace_host: Mapped[str] = mapped_column(String(255))
    app_name: Mapped[str] = mapped_column(String(100))
    # registered -> (heartbeats) -> decommissioned. Liveness is derived from
    # last_heartbeat_at at read time, not stored.
    status: Mapped[str] = mapped_column(String(20), default="registered")
    # sha256 of the per-deployment heartbeat token. The token itself is shown
    # once, at registration or rotation, and never stored.
    token_hash: Mapped[str] = mapped_column(String(64))
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_health_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    app_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # GitHub Environment holding this customer's Databricks deploy credentials.
    # The Control Plane stores only its name; the credentials never leave GitHub.
    github_environment: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    customer: Mapped[Customer] = relationship(back_populates="deployments")
    deploy_runs: Mapped[list[DeployRun]] = relationship(
        back_populates="deployment", order_by="DeployRun.requested_at.desc()"
    )


class DeployRun(Base):
    """One request to (re)deploy a Data Plane, executed by GitHub Actions.

    requested -> running -> succeeded | failed. The workflow reports each
    transition back; the Control Plane never talks to the customer workspace.
    """

    __tablename__ = "deploy_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    deployment_id: Mapped[str] = mapped_column(ForeignKey("deployments.id"), index=True)
    mode: Mapped[str] = mapped_column(String(20))
    ref: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="requested")
    run_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    deployment: Mapped[Deployment] = relationship(back_populates="deploy_runs")
