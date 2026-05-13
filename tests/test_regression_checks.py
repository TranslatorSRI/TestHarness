"""Tests for the regression-check framework and the edge-predicate check."""

import copy

import pytest

from test_harness.regression_checks import (
    EdgePredicateMatchCheck,
    RegressionStatus,
    run_all,
)
from test_harness.regression_checks.base import (
    CHECKS,
    RegressionCheckResult,
)
from tests.helpers.mock_responses import kp_response


def _query_graph():
    return copy.deepcopy(kp_response["message"]["query_graph"])


def _message_with_kg_predicate(predicate: str):
    """Return a kp_response copy whose single KG edge has the given predicate."""
    message = copy.deepcopy(kp_response["message"])
    message["knowledge_graph"]["edges"]["n0n1"]["predicate"] = predicate
    return message


def test_exact_predicate_match_passes():
    check = EdgePredicateMatchCheck()
    result = check.run(_message_with_kg_predicate("biolink:treats"), _query_graph())
    assert result.status == RegressionStatus.PASSED


def test_descendant_predicate_passes():
    # biolink:ameliorates_condition is a descendant of biolink:treats
    check = EdgePredicateMatchCheck()
    result = check.run(
        _message_with_kg_predicate("biolink:ameliorates_condition"),
        _query_graph(),
    )
    assert result.status == RegressionStatus.PASSED


def test_ancestor_predicate_fails():
    # biolink:affects is an ancestor of biolink:treats — too general
    check = EdgePredicateMatchCheck()
    result = check.run(
        _message_with_kg_predicate("biolink:affects"),
        _query_graph(),
    )
    assert result.status == RegressionStatus.FAILED
    assert result.details["count"] == 1
    assert result.details["mismatches"][0]["predicate"] == "biolink:affects"


def test_unrelated_predicate_fails():
    check = EdgePredicateMatchCheck()
    result = check.run(
        _message_with_kg_predicate("biolink:related_to"),
        _query_graph(),
    )
    assert result.status == RegressionStatus.FAILED


def test_pathfinder_query_graph_skipped():
    # Pathfinder-shaped query graphs have no edges with predicates.
    check = EdgePredicateMatchCheck()
    qg = {"nodes": {"n0": {}, "n1": {}}, "edges": {}}
    result = check.run(_message_with_kg_predicate("biolink:treats"), qg)
    assert result.status == RegressionStatus.SKIPPED


def test_query_graph_edge_without_predicates_skipped():
    check = EdgePredicateMatchCheck()
    qg = _query_graph()
    qg["edges"]["n0n1"]["predicates"] = []
    result = check.run(_message_with_kg_predicate("biolink:treats"), qg)
    assert result.status == RegressionStatus.SKIPPED


def test_dangling_edge_binding_fails():
    check = EdgePredicateMatchCheck()
    message = copy.deepcopy(kp_response["message"])
    # remove the kg edge that the binding references
    del message["knowledge_graph"]["edges"]["n0n1"]
    result = check.run(message, _query_graph())
    assert result.status == RegressionStatus.FAILED
    assert result.details["mismatches"][0]["reason"] == "kg_edge_missing"


def test_no_results_passes_vacuously():
    check = EdgePredicateMatchCheck()
    message = copy.deepcopy(kp_response["message"])
    message["results"] = []
    result = check.run(message, _query_graph())
    assert result.status == RegressionStatus.PASSED


def test_toolkit_init_failure_skipped():
    """If bmt cannot initialize, the check skips rather than crashing the run."""
    check = EdgePredicateMatchCheck()
    check._toolkit = None
    check._toolkit_init_error = "RuntimeError: simulated bmt failure"
    # Force _get_toolkit to short-circuit by leaving _toolkit None and
    # _toolkit_init_error populated (matches the post-failure state).
    original = check._get_toolkit
    check._get_toolkit = lambda: None
    try:
        result = check.run(
            _message_with_kg_predicate("biolink:treats"), _query_graph()
        )
    finally:
        check._get_toolkit = original
    assert result.status == RegressionStatus.SKIPPED
    assert "simulated bmt failure" in (result.message or "")


def test_run_all_isolates_crashing_checks():
    """A buggy check should not crash run_all."""

    class CrashingCheck:
        name = "crasher"

        def run(self, message, query_graph):
            raise ValueError("kaboom")

    CHECKS.append(CrashingCheck())
    try:
        results = run_all(
            _message_with_kg_predicate("biolink:treats"),
            _query_graph(),
        )
    finally:
        CHECKS.pop()

    crasher_results = [r for r in results if r.name == "crasher"]
    assert len(crasher_results) == 1
    assert crasher_results[0].status == RegressionStatus.SKIPPED
    assert "kaboom" in (crasher_results[0].message or "")
    # the real edge_predicate check still ran
    assert any(r.name == "edge_predicate_match" for r in results)


def test_run_all_returns_one_result_per_check():
    results = run_all(
        _message_with_kg_predicate("biolink:treats"),
        _query_graph(),
    )
    assert len(results) == len(CHECKS)
    for r in results:
        assert isinstance(r, RegressionCheckResult)
