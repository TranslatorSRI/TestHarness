import pytest

from test_harness.main import main
from .example_tests import example_tests


def test_main(mocker):
    """Test the main function."""
    # This article is awesome: https://nedbatchelder.com/blog/201908/why_your_mock_doesnt_work.html
    run_ui_test = mocker.patch("test_harness.run.run_ui_test", return_value="Pass")
    run_ars_test = mocker.patch("test_harness.run.run_ars_test", return_value="Fail")
    main(
        {
            "tests": example_tests,
            "save_to_dashboard": False,
            "json_output": False,
            "log_level": "ERROR",
        }
    )
    run_ui_test.assert_called_once()
    run_ars_test.assert_called_once()
