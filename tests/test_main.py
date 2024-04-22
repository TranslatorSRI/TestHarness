import pytest

from test_harness.main import main
from .example_test_inputs import example_acceptance_test_cases

from .mocker import (
    MockReporter,
    MockSlacker,
)


@pytest.mark.asyncio
async def test_main(mocker):
    """Test the main function."""
    # This article is awesome: https://nedbatchelder.com/blog/201908/why_your_mock_doesnt_work.html
    run_ars_test = mocker.patch("test_harness.run.run_ars_test", return_value="Fail")
    run_tests = mocker.patch("test_harness.main.run_tests", return_value={})
    mocker.patch("test_harness.slacker.Slacker", return_value=MockSlacker())
    mocker.patch("test_harness.main.Reporter", return_value=MockReporter())
    await main(
        {
            "tests": example_acceptance_test_cases,
            "save_to_dashboard": False,
            "json_output": False,
            "log_level": "ERROR",
        }
    )
    # run_ui_test.assert_called_once()
    run_tests.assert_called_once()
