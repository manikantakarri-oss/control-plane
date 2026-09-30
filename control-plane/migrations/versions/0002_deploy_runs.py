"""github_environment on deployments; deploy_runs

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-30
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("deployments") as batch:
        batch.add_column(sa.Column("github_environment", sa.String(100), nullable=True))
    op.create_table(
        "deploy_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("deployment_id", sa.String(36), sa.ForeignKey("deployments.id"), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("ref", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("run_url", sa.String(500), nullable=True),
        sa.Column("detail", sa.String(500), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_deploy_runs_deployment_id", "deploy_runs", ["deployment_id"])


def downgrade() -> None:
    op.drop_index("ix_deploy_runs_deployment_id", table_name="deploy_runs")
    op.drop_table("deploy_runs")
    with op.batch_alter_table("deployments") as batch:
        batch.drop_column("github_environment")
