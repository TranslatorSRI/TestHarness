"""Performance message

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-30

A performance result can carry a message, eg why the suite skips the test.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("performance_results", sa.Column("message", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("performance_results", "message")
