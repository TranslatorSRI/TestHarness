"""Test the Harness run file."""

import pytest
from pytest_httpx import HTTPXMock

from test_harness.run import run_tests

from .helpers.example_tests import example_test_cases
from .helpers.mocks import (
    MockReporter,
    MockSlacker,
    MockQueryRunner,
)
from .helpers.logger import setup_logger
from .helpers.mock_responses import kp_response

logger = setup_logger()


@pytest.mark.asyncio
async def test_run_tests(mocker, httpx_mock: HTTPXMock):
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
        url="https://nodenorm.ci.transltr.io/get_normalized_nodes",
        json={
            "MONDO:0010794": None,
            "DRUGBANK:DB00313": None,
            "MESH:D001463": None,
        },
    )
    full_report = await run_tests(
        reporter=MockReporter(
            base_url="http://test",
        ),
        slacker=MockSlacker(),
        tests=example_test_cases,
        logger=logger,
        args={
            "suite": "testing",
            "trapi_version": "1.5.0",
        },
    )
    assert full_report["SKIPPED"] == 2
