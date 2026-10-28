"""The harness's exit status reports whether the run happened, not how it went.

A scheduled sweep must not go red because a service was slow or a checkpoint
missed -- those are results, and they reach Slack. It must go red when the
harness couldn't carry out the run at all, because that job produced nothing and
would otherwise sit green and silent.
"""

from unittest import mock

from test_harness.main import EXIT_COULD_NOT_RUN, EXIT_OK, main
from test_harness.sweep import run_sweep

from .helpers.example_tests import example_test_cases
from .helpers.mocks import MockReporter, MockSlacker
from .test_reporting import _checkpoint, _helmsdeep_summary

BASE_ARGS = {
    "suite": "testing",
    "save_to_dashboard": False,
    "json_output": False,
    "log_level": "ERROR",
}

TARGETS = [("ars", "http://ars"), ("aragorn", "http://aragorn")]


def _run(mocker, **overrides):
    mocker.patch("test_harness.main.Slacker", return_value=MockSlacker())
    mocker.patch("test_harness.main.Reporter", return_value=MockReporter())
    return main({**BASE_ARGS, **overrides})


def test_a_completed_run_exits_ok(mocker):
    mocker.patch("test_harness.main.run_tests", return_value={})
    assert _run(mocker, tests=example_test_cases) == EXIT_OK


def test_failing_tests_still_exit_ok(mocker):
    """Red results are news for Slack, not a broken cron job."""

    def fail_everything(tests, reporter, collector, logger, args):
        collector.has_acceptance_results = True
        collector.acceptance_report["FAILED"] = 12
        collector.has_performance_results = True
        collector.performance_report["stats"]["http://ara (aras_mixed)"] = {
            "host": "http://ara",
            "helmsdeep_target": "aras_mixed",
            "profile": "mixed",
            "protocol": "sync",
            "checkpoints": [_checkpoint(30, "FAIL")],
            "checkpoints_passed": False,
            "max_sustainable_concurrency": None,
            "summary": _helmsdeep_summary(checkpoints=[_checkpoint(30, "FAIL")]),
            "report_html": "<html></html>",
        }

    mocker.patch("test_harness.main.run_tests", side_effect=fail_everything)
    assert _run(mocker, tests=example_test_cases) == EXIT_OK


def test_a_performance_run_that_never_completed_still_exits_ok(mocker):
    """An unreachable service is a result too -- Slack says so; cron stays green."""

    def broken_run(tests, reporter, collector, logger, args):
        collector.has_performance_results = True
        collector.performance_report["stats"]["http://ara (aras)"] = {
            "host": "http://ara",
            "helmsdeep_target": "aras",
            "error": "HelmsDeep exited 2 without writing a summary",
            "checkpoints": [],
            "summary": None,
            "report_html": None,
        }
        assert collector.performance_checkpoints_passed is False

    mocker.patch("test_harness.main.run_tests", side_effect=broken_run)
    assert _run(mocker, tests=example_test_cases) == EXIT_OK


def test_half_configured_target_override_cannot_run(mocker):
    assert (
        _run(mocker, tests=example_test_cases, target_url="http://x")
        == EXIT_COULD_NOT_RUN
    )


def test_no_subcommand_cannot_run(mocker):
    """Neither a suite to download nor one to load nor inline tests."""
    mocker.patch("test_harness.main.Slacker", return_value=MockSlacker())
    mocker.patch("test_harness.main.Reporter", return_value=MockReporter())
    assert main(dict(BASE_ARGS)) == EXIT_COULD_NOT_RUN


def test_a_suite_that_isnt_there_cannot_run(mocker):
    """A typo'd suite name in a CronJob must not be a silent green no-op."""
    mocker.patch("test_harness.main.load_tests", return_value={})
    assert (
        _run(mocker, tests_dir=None, suite="definitely_not_a_suite")
        == EXIT_COULD_NOT_RUN
    )


def test_filtering_everything_out_cannot_run(mocker):
    """Asking for MVP1 in a suite with none produced no results; say so."""
    mocker.patch("test_harness.main.run_tests", return_value={})
    mocker.patch("test_harness.main.filter_tests_by_query_type", return_value={})
    assert (
        _run(mocker, tests=example_test_cases, query_type="MVP1") == EXIT_COULD_NOT_RUN
    )


def test_sweep_is_green_when_every_run_happened():
    """The harness exits 0 for any test outcome, so a red sweep means the runs
    themselves broke -- not that a service failed its checkpoints."""
    with mock.patch(
        "test_harness.sweep.subprocess.run", return_value=mock.Mock(returncode=0)
    ):
        assert run_sweep(TARGETS, "perf") == 0


def test_sweep_is_red_only_when_a_run_could_not_be_carried_out():
    with mock.patch(
        "test_harness.sweep.subprocess.run",
        side_effect=[mock.Mock(returncode=0), mock.Mock(returncode=1)],
    ):
        assert run_sweep(TARGETS, "perf") == 1
