"""Initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-06 13:59:11.385508
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("suite", sa.String(length=255), nullable=False),
        sa.Column("env", sa.String(length=32), nullable=True),
        sa.Column("target", sa.String(length=255), nullable=True),
        sa.Column("target_url", sa.Text(), nullable=True),
        sa.Column("query_type", sa.String(length=32), nullable=True),
        sa.Column("harness_version", sa.String(length=64), nullable=True),
        sa.Column("tests_source", sa.Text(), nullable=True),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.Column("origin_ref", sa.String(length=255), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("counts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_runs_series", "runs", ["suite", "env", "started_at"], unique=False
    )
    op.create_index("ix_runs_started_at", "runs", ["started_at"], unique=False)
    op.create_index(
        "uq_runs_origin_ref",
        "runs",
        ["origin", "origin_ref"],
        unique=True,
        postgresql_where="origin_ref IS NOT NULL",
    )
    op.create_table(
        "asset_results",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("test_case_id", sa.String(length=255), nullable=False),
        sa.Column("asset_id", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("expected_output", sa.String(length=64), nullable=True),
        sa.Column("predicate", sa.String(length=255), nullable=True),
        sa.Column("input_curie", sa.String(length=255), nullable=True),
        sa.Column("output_curie", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("parent_pk", sa.String(length=255), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "test_case_id", "asset_id"),
    )
    op.create_index(
        "ix_asset_results_asset",
        "asset_results",
        ["test_case_id", "asset_id"],
        unique=False,
    )
    op.create_table(
        "performance_results",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("test_case_id", sa.String(length=255), nullable=False),
        sa.Column("asset_id", sa.String(length=255), nullable=False),
        sa.Column("host", sa.String(length=512), nullable=False),
        sa.Column("helmsdeep_target", sa.String(length=64), nullable=True),
        sa.Column("profile", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("max_sustainable_concurrency", sa.Float(), nullable=True),
        sa.Column("knee_unsupported", sa.Boolean(), nullable=True),
        sa.Column("checkpoints_passed", sa.Boolean(), nullable=True),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "test_case_id", "asset_id", "host"),
    )
    op.create_index(
        "ix_performance_results_host", "performance_results", ["host"], unique=False
    )
    op.create_table(
        "agent_results",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("asset_result_id", sa.Integer(), nullable=False),
        sa.Column("agent", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("found", sa.Boolean(), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("n_results", sa.Integer(), nullable=True),
        sa.Column("response_time_s", sa.Float(), nullable=True),
        sa.Column("pk", sa.String(length=255), nullable=True),
        sa.Column("expected_nodes_found", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["asset_result_id"], ["asset_results.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asset_result_id", "agent"),
    )
    op.create_index("ix_agent_results_agent", "agent_results", ["agent"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_agent_results_agent", table_name="agent_results")
    op.drop_table("agent_results")
    op.drop_index("ix_performance_results_host", table_name="performance_results")
    op.drop_table("performance_results")
    op.drop_index("ix_asset_results_asset", table_name="asset_results")
    op.drop_table("asset_results")
    op.drop_index(
        "uq_runs_origin_ref",
        table_name="runs",
        postgresql_where="origin_ref IS NOT NULL",
    )
    op.drop_index("ix_runs_started_at", table_name="runs")
    op.drop_index("ix_runs_series", table_name="runs")
    op.drop_table("runs")
