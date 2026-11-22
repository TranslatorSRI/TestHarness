"""Pathfinder NDCG

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-05

A Pathfinder agent result carries the NDCG of its ranked paths.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("agent_results", sa.Column("ndcg", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_results", "ndcg")
