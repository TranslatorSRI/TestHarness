"""Regression checks run alongside acceptance/pathfinder pass-fail analysis."""

from test_harness.regression_checks.base import (
    CHECKS,
    RegressionCheck,
    RegressionCheckResult,
    RegressionStatus,
    run_all,
)
from test_harness.regression_checks.edge_predicate import EdgePredicateMatchCheck

CHECKS.append(EdgePredicateMatchCheck())

__all__ = [
    "CHECKS",
    "RegressionCheck",
    "RegressionCheckResult",
    "RegressionStatus",
    "EdgePredicateMatchCheck",
    "run_all",
]
