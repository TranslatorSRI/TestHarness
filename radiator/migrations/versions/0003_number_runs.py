"""Number runs

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-10

Existing runs are numbered by start time; new ones get the next number as
they arrive.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE run_number_seq")
    op.add_column("runs", sa.Column("number", sa.Integer(), nullable=True))
    op.execute("""
        UPDATE runs SET number = numbered.n
        FROM (
            SELECT id, row_number() OVER (ORDER BY started_at, id) AS n FROM runs
        ) AS numbered
        WHERE runs.id = numbered.id
        """)
    op.execute(
        "SELECT setval('run_number_seq', COALESCE((SELECT max(number) FROM runs), 0) + 1, false)"
    )
    op.alter_column(
        "runs",
        "number",
        nullable=False,
        server_default=sa.text("nextval('run_number_seq')"),
    )
    op.create_unique_constraint("runs_number_key", "runs", ["number"])


def downgrade() -> None:
    op.drop_constraint("runs_number_key", "runs", type_="unique")
    op.drop_column("runs", "number")
    op.execute("DROP SEQUENCE run_number_seq")
