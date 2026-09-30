"""customers and deployments

Revision ID: 0001
Revises:
Create Date: 2026-09-30
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "deployments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("customer_id", sa.String(36), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("workspace_host", sa.String(255), nullable=False),
        sa.Column("app_name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_health_status", sa.String(20), nullable=True),
        sa.Column("app_version", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_host", "app_name", name="uq_deployment_workspace_app"),
    )
    op.create_index("ix_deployments_customer_id", "deployments", ["customer_id"])


def downgrade() -> None:
    op.drop_index("ix_deployments_customer_id", table_name="deployments")
    op.drop_table("deployments")
    op.drop_table("customers")
