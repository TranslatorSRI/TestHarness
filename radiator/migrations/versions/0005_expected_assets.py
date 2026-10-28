"""Expected assets

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-14

How many assets a run will test, for its progress while it's running.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("expected_assets", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "expected_assets")
