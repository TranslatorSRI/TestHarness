"""Run cycles

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-10

Runs started by ``test-harness-cycle`` share a cycle id.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("cycle_id", sa.Uuid(), nullable=True))
    op.create_index("ix_runs_cycle_id", "runs", ["cycle_id"])


def downgrade() -> None:
    op.drop_index("ix_runs_cycle_id", table_name="runs")
    op.drop_column("runs", "cycle_id")
