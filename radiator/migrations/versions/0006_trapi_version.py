"""TRAPI version

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-15

The TRAPI version a run's queries were written in.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("trapi_version", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "trapi_version")
