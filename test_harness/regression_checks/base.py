"""Pluggable regression checks for TRAPI responses."""

from dataclasses import dataclass, field
from enum import Enum
import logging
from typing import Any, Dict, List, Optional, Protocol


class RegressionStatus(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


@dataclass
class RegressionCheckResult:
    name: str
    status: RegressionStatus
    message: Optional[str] = None
    details: Optional[Dict[str, Any]] = field(default=None)


class RegressionCheck(Protocol):
    name: str

    def run(
        self, message: Dict[str, Any], query_graph: Dict[str, Any]
    ) -> RegressionCheckResult: ...


CHECKS: List[RegressionCheck] = []


def run_all(
    message: Dict[str, Any],
    query_graph: Dict[str, Any],
    logger: Optional[logging.Logger] = None,
) -> List[RegressionCheckResult]:
    """Run every registered regression check, isolating failures per-check."""
    results: List[RegressionCheckResult] = []
    for check in CHECKS:
        try:
            results.append(check.run(message, query_graph))
        except Exception as e:
            if logger is not None:
                logger.warning(
                    f"Regression check {getattr(check, 'name', type(check).__name__)} crashed: {e}"
                )
            results.append(
                RegressionCheckResult(
                    name=getattr(check, "name", type(check).__name__),
                    status=RegressionStatus.SKIPPED,
                    message=f"Check crashed: {type(e).__name__}: {e}",
                )
            )
    return results
