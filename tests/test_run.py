import pytest

from test_harness.run import run_tests
from example_test_outputs import example_one_hops_test_output
from example_test_inputs import (
    example_acceptance_test_cases,
    example_one_hop_test_cases
)
from .mocker import (
    MockReporter,
    MockSlacker,
)


@pytest.mark.asyncio
async def test_run_tests(mocker):
    """Test the run_tests function."""
    # This article is awesome: https://nedbatchelder.com/blog/201908/why_your_mock_doesnt_work.html
    run_ars_test = mocker.patch(
        "test_harness.run.run_ars_test",
        return_value={
            "pks": {
                "parent_pk": "123abc",
                "merged_pk": "456def",
            },
            "results": [
                {
                    "ars": {
                        "status": "PASSED",
                    },
                },
                {
                    "ars": {
                        "status": "FAILED",
                        "message": "",
                    },
                },
            ],
        },
    )
    # run_benchmarks = mocker.patch("test_harness.run.run_benchmarks", return_value={})
    await run_tests(
        reporter=MockReporter(
            base_url="http://test",
        ),
        slacker=MockSlacker(),
        tests=example_acceptance_test_cases,
    )
    # run_ui_test.assert_called_once()
    run_ars_test.assert_called_once()
    # run_benchmarks.assert_not_called()


@pytest.mark.asyncio
async def test_one_hop_test_run_tests(mocker):
    """Test the run_tests function on Standards Validation TestRunner."""
    run_one_hop_tests = mocker.patch(
        "test_harness.run.run_one_hop_tests",
        return_value=example_one_hops_test_output
    )
    await run_tests(
        reporter=MockReporter(
            base_url="http://test",
        ),
        slacker=MockSlacker(),
        tests=example_one_hop_test_cases
    )
    run_one_hop_tests.assert_called_once()
