"""Tests the suite skips: not run, but reported as SKIPPED with the reason."""

# first, like the real entry point: test_harness.main applies gevent's
# monkey-patching, which has to come before anything else starts a thread
from test_harness.main import cli

import sys
from unittest import mock

from test_harness.result_collector import ResultCollector
from test_harness.radiator_client import RadiatorClient
from test_harness.run import run_tests
from test_harness.utils import AgentStatus, skipped_because

from .helpers.example_tests import example_test_cases
from .helpers.logger import setup_logger
from .helpers.mocks import MockQueryRunner
from .test_radiator import _run
from .test_reporting import _performance_test_case, _RecordingReporter

logger = setup_logger()


class _SpyQueryRunner(MockQueryRunner):
    """Records the test cases it's asked to query; answers nothing."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.queried = []

    def run_queries(self, test_case):
        self.queried.append(test_case)
        return {}, {}


def _cases(**skips):
    """The example acceptance cases, with ``skips`` set on assets or cases by
    id, eg ``Asset_3={"skip": True}``."""
    cases = {}
    for case_id, case in example_test_cases.items():
        assets = [
            asset.model_copy(update=skips.get(asset.id, {}))
            for asset in case.test_assets
        ]
        cases[case_id] = case.model_copy(
            update={"test_assets": assets, **skips.get(case_id, {})}
        )
    return cases


def _run_tests(mocker, tests, **args):
    runner = _SpyQueryRunner(logger, "ci")
    mocker.patch("test_harness.run.QueryRunner", return_value=runner)
    radiator = RadiatorClient(enabled=False)
    radiator.start_run(_run())
    collector = ResultCollector("ci", logger, radiator=radiator)
    reporter = _RecordingReporter(base_url="http://ir")
    run_tests(
        tests=tests,
        reporter=reporter,
        collector=collector,
        logger=logger,
        args={"suite": "acceptance", "env": "ci", "trapi_version": "2.0.0", **args},
    )
    results = {r.asset_id: r for r in radiator.payload.results}
    return runner, reporter, collector, results


def test_skipped_because():
    case = example_test_cases["TestCase_1"]
    asset = case.test_assets[0]
    assert skipped_because(case, asset) is None
    skipped = asset.model_copy(update={"skip": True, "skip_reason": "Tests#140"})
    assert skipped_because(case, skipped) == "Skipped in the suite: Tests#140"
    assert skipped_because(case, skipped, include_skipped=True) is None
    # skipping the test case skips every asset in it
    skipped_case = case.model_copy(update={"skip": True})
    assert skipped_because(skipped_case, asset) == "Skipped in the suite"


def test_a_skipped_asset_isnt_queried_but_is_reported(mocker):
    tests = _cases(Asset_3={"skip": True, "skip_reason": "always fails: Tests#140"})
    runner, reporter, collector, results = _run_tests(mocker, tests)

    queried = {case.id: [a.id for a in case.test_assets] for case in runner.queried}
    assert queried["TestCase_1"] == ["Asset_4"]
    # still reported, as SKIPPED with the reason, for every agent
    skipped = results["Asset_3"]
    assert skipped.status == AgentStatus.SKIPPED.value
    assert {a.status for a in skipped.agents} == {AgentStatus.SKIPPED.value}
    assert {a.message for a in skipped.agents} == {
        "Skipped in the suite: always fails: Tests#140"
    }
    # the asset that ran has no such message
    assert {a.message for a in results["Asset_4"].agents} == {None}
    assert len(reporter.finished) == 3
    assert collector.acceptance_report[AgentStatus.SKIPPED.value] == 3


def test_a_skipped_test_case_isnt_queried_at_all(mocker):
    tests = _cases(TestCase_1={"skip": True, "skip_reason": "ARS times out"})
    runner, _, _, results = _run_tests(mocker, tests)

    assert "TestCase_1" not in {case.id for case in runner.queried}
    for asset_id in ("Asset_3", "Asset_4"):
        assert {a.message for a in results[asset_id].agents} == {
            "Skipped in the suite: ARS times out"
        }


def test_include_skipped_runs_them_anyway(mocker):
    tests = _cases(TestCase_1={"skip": True}, Asset_3={"skip": True})
    runner, _, _, results = _run_tests(mocker, tests, include_skipped=True)

    queried = {case.id: [a.id for a in case.test_assets] for case in runner.queried}
    assert queried["TestCase_1"] == ["Asset_3", "Asset_4"]
    assert {a.message for r in results.values() for a in r.agents} == {None}


def test_a_skipped_performance_test_doesnt_load_the_service(mocker):
    mocker.patch(
        "test_harness.run.QueryRunner", return_value=MockQueryRunner(logger, "ci")
    )
    load_test = mocker.patch("test_harness.run.run_performance_test")
    radiator = RadiatorClient(enabled=False)
    radiator.start_run(_run())
    reporter = _RecordingReporter(base_url="http://ir")
    case = _performance_test_case().model_copy(
        update={"skip": True, "skip_reason": "ARS is being rebuilt"}
    )
    run_tests(
        tests={case.id: case},
        reporter=reporter,
        collector=ResultCollector("ci", logger, radiator=radiator),
        logger=logger,
        args={"suite": "perf", "env": "ci", "trapi_version": "2.0.0"},
    )

    load_test.assert_not_called()
    assert [result for _, result in reporter.finished] == [AgentStatus.SKIPPED.value]
    [perf] = radiator.payload.performance
    assert perf.status == AgentStatus.SKIPPED.value
    assert perf.message == "Skipped in the suite: ARS is being rebuilt"


def test_include_skipped_is_a_harness_option():
    with mock.patch(
        "test_harness.main.main", return_value=0
    ) as main, mock.patch.object(
        sys, "argv", ["test-harness", "--env", "ci", "--include_skipped", "load", "s"]
    ):
        try:
            cli()
        except SystemExit:
            pass
    assert main.call_args.args[0]["include_skipped"] is True
