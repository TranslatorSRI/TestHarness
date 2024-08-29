import pytest

from test_harness.main import main

from .helpers.example_tests import example_test_cases
from .helpers.mocks import (
    MockReporter,
    MockSlacker,
)


@pytest.mark.asyncio
async def test_main(mocker):
    """Test the main function."""
    # This article is awesome: https://nedbatchelder.com/blog/201908/why_your_mock_doesnt_work.html
    run_tests = mocker.patch("test_harness.main.run_tests", return_value={})
    mocker.patch("test_harness.main.Slacker", return_value=MockSlacker())
    mocker.patch("test_harness.main.Reporter", return_value=MockReporter())
    await main(
        {
            "tests": example_test_cases,
            "suite": "testing",
            "save_to_dashboard": False,
            "json_output": False,
            "log_level": "ERROR",
        }
    )
    run_tests.assert_called_once()
