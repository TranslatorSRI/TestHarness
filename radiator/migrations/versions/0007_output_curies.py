"""Output CURIEs

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-29

An asset's expected output can go by several CURIEs, so its one
``output_curie`` becomes the list ``output_curies``; existing rows keep their
CURIE as a one-item list. Each agent's result also records which of them came
back (``found_curies``).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "asset_results",
        sa.Column(
            "output_curies",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.execute(
        "UPDATE asset_results SET output_curies = jsonb_build_array(output_curie) "
        "WHERE output_curie IS NOT NULL AND output_curie <> ''"
    )
    op.alter_column("asset_results", "output_curies", server_default=None)
    op.drop_column("asset_results", "output_curie")
    op.add_column(
        "agent_results",
        sa.Column(
            "found_curies", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_results", "found_curies")
    op.add_column(
        "asset_results",
        sa.Column("output_curie", sa.String(length=255), nullable=True),
    )
    # only the first CURIE fits in the old column
    op.execute("UPDATE asset_results SET output_curie = output_curies ->> 0")
    op.drop_column("asset_results", "output_curies")
