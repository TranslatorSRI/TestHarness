"""Suites that were renamed, so each suite's history stays one line.

The Tests repo used to keep a copy of a suite per environment (eg
``prod_integration`` was ``sprint_6_tests`` for prod). Now each suite is run
in every environment, and a run's ``env`` says which. Runs recorded under an
old name are filed under the current one, and keep the name they were
recorded with in ``Run.source_suite``.

Migration 0008 renamed the runs already recorded with a copy of this table;
ingest renames any that arrive later, eg a run saved before the switch and
pushed after it.
"""

RENAMED_SUITES = {
    "sprint_6_tests": "mvp_tests",
    "dev_refactor_tests": "mvp_tests",
    "test_integration": "mvp_tests",
    "prod_integration": "mvp_tests",
    "pathfinder_tests_TEST": "pathfinder_tests",
    "pathfinder_tests_PROD": "pathfinder_tests",
}


def current_suite(suite: str) -> str:
    """The name a suite goes by now."""
    return RENAMED_SUITES.get(suite, suite)
