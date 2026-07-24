"""Test the Harness run file."""

import pytest
from pytest_httpx import HTTPXMock

from test_harness.run import run_tests

from .helpers.example_tests import example_test_cases
from .helpers.mocks import (
    MockReporter,
    MockResultCollector,
    MockSlacker,
    MockQueryRunner,
)
from .helpers.logger import setup_logger
from .helpers.mock_responses import kp_response

logger = setup_logger()


def test_run_tests(mocker, httpx_mock: HTTPXMock):
    """Test the run_tests function."""
    # This article is awesome: https://nedbatchelder.com/blog/201908/why_your_mock_doesnt_work.html
    mocker.patch(
        "test_harness.run.QueryRunner",
        return_value=MockQueryRunner(logger),
    )
    httpx_mock.add_response(
        url="http://tester/query",
        json=kp_response,
    )
    httpx_mock.add_response(
        url="https://nodenorm-es.ci.transltr.io/get_normalized_nodes",
        json={
            "MONDO:0010794": None,
            "DRUGBANK:DB00313": None,
            "MESH:D001463": None,
            "CHEBI:18295": None,
            "CHEBI:31690": None,
            "CL:0000097": None,
            "MONDO:0004979": None,
            "NCBIGene:3815": None,
            "NCBIGene:4254": None,
            "PR:000049994": None,
        },
    )
    run_tests(
        tests=example_test_cases,
        reporter=MockReporter(
            base_url="http://test",
        ),
        collector=MockResultCollector("dev", logger),
        logger=logger,
        args={
            "suite": "testing",
            "trapi_version": "1.6.0",
        },
    )


def test_run_tests_with_target_override(httpx_mock: HTTPXMock):
    """Test that a target override sends queries to the given service.

    The example tests specify the ars component, but with an override every
    query should go straight to the local service, the SmartAPI registry
    should never be fetched, and results should be collected for the
    override agent only.
    """
    httpx_mock.add_response(
        url="http://localhost:8080/query",
        json=kp_response,
    )
    httpx_mock.add_response(
        url="https://nodenorm-es.ci.transltr.io/get_normalized_nodes",
        json={
            "MONDO:0010794": None,
            "DRUGBANK:DB00313": None,
            "MESH:D001463": None,
            "CHEBI:18295": None,
            "CHEBI:31690": None,
            "CL:0000097": None,
            "MONDO:0004979": None,
            "NCBIGene:3815": None,
            "NCBIGene:4254": None,
            "PR:000049994": None,
        },
    )
    collector = MockResultCollector("ci", logger, target="aragorn")
    assert collector.agents == ["aragorn"]
    run_tests(
        tests=example_test_cases,
        reporter=MockReporter(
            base_url="http://test",
        ),
        collector=collector,
        logger=logger,
        args={
            "suite": "testing",
            "trapi_version": "1.6.0",
            "target_url": "http://localhost:8080",
            "target": "aragorn",
        },
    )
    # every asset was recorded against the override agent
    total_results = sum(
        count
        for query_type in collector.acceptance_stats["aragorn"].values()
        for count in query_type.values()
    )
    assert total_results > 0
