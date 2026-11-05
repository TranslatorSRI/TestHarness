"""Shared suites

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-29

Each suite used to have a copy per environment; now one suite runs in every
environment, and a run's env says which. Runs of the old copies are renamed to
the shared suite, so its history (the trends, an asset's history, the diff
against the previous run) carries on from them. The name a run was recorded
with is kept in ``source_suite``.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# A copy of radiator.suites.RENAMED_SUITES as it was for this migration.
RENAMED_SUITES = {
    "sprint_6_tests": "mvp_tests",
    "dev_refactor_tests": "mvp_tests",
    "test_integration": "mvp_tests",
    "prod_integration": "mvp_tests",
    "pathfinder_tests_TEST": "pathfinder_tests",
    "pathfinder_tests_PROD": "pathfinder_tests",
}


def upgrade() -> None:
    op.add_column("runs", sa.Column("source_suite", sa.String(255), nullable=True))
    runs = sa.table(
        "runs", sa.column("suite", sa.String), sa.column("source_suite", sa.String)
    )
    for old, new in RENAMED_SUITES.items():
        op.execute(
            runs.update().where(runs.c.suite == old).values(suite=new, source_suite=old)
        )


def downgrade() -> None:
    op.execute("UPDATE runs SET suite = source_suite WHERE source_suite IS NOT NULL")
    op.drop_column("runs", "source_suite")
