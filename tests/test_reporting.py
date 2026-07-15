"""Regression tests for test-result propagation to reporting services.

These cover bugs where results were dropped or mangled on their way to the
Information Radiator (Reporter) and/or Slack (via the ResultCollector output):

* A skipped test case left its assets marked FAILED (for ARAs) / NO_RESULTS
  (for ARS) instead of SKIPPED, and assets that never got a query were dropped
  from the per-agent stats entirely.
* Performance tests were created in the radiator but never finished.
* Performance failures were overwritten per host instead of accumulated.
* Agents that returned no response were written to the CSV but omitted from
  the per-agent JSON stats.
"""

from translator_testing_model.datamodel.pydanticmodel import (
    ComponentEnum,
    PerformanceTestCase,
    TestEnvEnum,
    TestObjectiveEnum,
)

from test_harness.result_collector import ResultCollector
from test_harness.run import run_tests
from test_harness.utils import AgentReport, AgentStatus, TestReport

from .helpers.example_tests import example_test_cases
from .helpers.logger import setup_logger
from .helpers.mocks import MockReporter, MockResultCollector, MockQueryRunner

logger = setup_logger()


class _Asset:
    name = "asset-name"
    id = "asset-1"
    expected_output = "TopAnswer"


class _Case:
    id = "case-1"


def test_skipped_agents_are_counted_in_stats():
    """An agent with no response should be recorded as SKIPPED in the JSON
    stats, not just the CSV, so the two Slack artifacts agree."""
    collector = ResultCollector("dev", logger)
    # Only "ars" responded; the shepherd agents should be counted SKIPPED.
    report = TestReport(
        pks={},
        result={
            "ars": AgentReport(
                status=AgentStatus.PASSED, message=None, actual_output=None
            )
        },
        test_details=None,
    )
    collector.collect_acceptance_result(_Case(), _Asset(), report, "pk", "http://ir/1")

    for agent in collector.agents:
        total = sum(collector.acceptance_stats[agent]["TopAnswer"].values())
        assert total == 1, f"{agent} should have exactly one recorded result"
    assert collector.acceptance_stats["ars"]["TopAnswer"]["PASSED"] == 1
    for agent in collector.agents:
        if agent == "ars":
            continue
        assert collector.acceptance_stats[agent]["TopAnswer"]["SKIPPED"] == 1

    # CSV and JSON should agree: the CSV row lists PASSED then three SKIPPED.
    csv_row = collector.acceptance_csv.strip().splitlines()[-1]
    assert csv_row.count("SKIPPED") == 3
    assert "PASSED" in csv_row


def _perf_results(target, failures):
    return {
        "stats": [],
        "failures": failures,
        "test_run_time": 10,
        "spawn_rate": 1,
        "target": target,
        "query_response_sizes": {},
        "stats_history": [],
        "summary_html": None,
    }


def test_performance_failures_accumulate_across_hosts():
    """Failures from every performance target must survive to the summary."""
    collector = ResultCollector("prod", logger)
    collector.collect_performance_result(
        _Case(),
        _Asset(),
        "http://ir/1",
        "hostA",
        _perf_results(
            "ars",
            {
                "k1": {
                    "method": "POST",
                    "name": "submit",
                    "error": "x",
                    "occurrences": 3,
                }
            },
        ),
    )
    collector.collect_performance_result(
        _Case(),
        _Asset(),
        "http://ir/2",
        "hostB",
        _perf_results(
            "ars",
            {
                "k1": {
                    "method": "POST",
                    "name": "submit",
                    "error": "x",
                    "occurrences": 2,
                },
                "k2": {"method": "GET", "name": "poll", "error": "y", "occurrences": 5},
            },
        ),
    )
    failures = collector.performance_report["failures"]
    assert set(failures) == {"k1", "k2"}
    # k1 seen on both hosts -> occurrences summed.
    assert failures["k1"]["occurrences"] == 5
    assert failures["k2"]["occurrences"] == 5
    total = sum(f.get("occurrences", 0) for f in failures.values())
    assert total == 10


class _RecordingReporter(MockReporter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.finished = []
        self.labels = []

    def finish_test(self, test_id, result):
        self.finished.append((test_id, result))
        return result

    def upload_labels(self, test_id, labels):
        self.labels.append(labels)


def test_force_skipped_records_all_agents_skipped():
    """A skipped test must mark every agent SKIPPED, even when the report
    carries incidental per-agent statuses from a partial/errored run."""
    collector = ResultCollector("dev", logger)
    report = TestReport(
        pks={},
        result={
            "ars": AgentReport(
                status=AgentStatus.NO_RESULTS, message=None, actual_output=None
            ),
            "shepherd-aragorn": AgentReport(
                status=AgentStatus.FAILED, message="boom", actual_output=None
            ),
        },
        test_details=None,
    )
    collector.collect_acceptance_result(
        _Case(), _Asset(), report, "pk", "http://ir/1", force_skipped=True
    )

    for agent in collector.agents:
        stats = collector.acceptance_stats[agent]["TopAnswer"]
        assert stats["SKIPPED"] == 1, agent
        assert stats["FAILED"] == 0 and stats["NO_RESULTS"] == 0, agent

    csv_row = collector.acceptance_csv.strip().splitlines()[-1]
    # every agent column is SKIPPED; nothing leaks FAILED/NO_RESULTS
    assert "FAILED" not in csv_row and "NO_RESULTS" not in csv_row
    assert csv_row.count("SKIPPED") == len(collector.agents)


def _performance_test_case():
    return PerformanceTestCase(
        id="perf-1",
        name="ExamplePerformanceTest",
        description="perf",
        tags=[],
        test_runner_settings=["inferred"],
        test_run_time=10,
        spawn_rate=0.1,
        query_type=None,
        test_assets=[
            {
                "id": "Asset_1",
                "name": "perf asset",
                "description": "perf asset",
                "tags": [],
                "test_runner_settings": ["inferred"],
                "input_id": "MONDO:0011426",
                "input_name": "Aceruloplasminemia",
                "input_category": "biolink:Disease",
                "predicate_id": "biolink:treats",
                "predicate_name": "treats",
                "output_id": "PUBCHEM.COMPOUND:23925",
                "output_name": "Iron",
                "output_category": "biolink:ChemicalEntity",
                "association": None,
                "qualifiers": [
                    {"parameter": "biolink_object_aspect_qualifier", "value": ""},
                    {"parameter": "biolink_object_direction_qualifier", "value": ""},
                ],
                "expected_output": "NeverShow",
            }
        ],
        preconditions=[],
        trapi_template=None,
        test_case_objective=TestObjectiveEnum.QuantitativeTest,
        test_case_source=None,
        test_case_predicate_name="treats",
        test_case_predicate_id="biolink:treats",
        test_case_input_id="MONDO:0011426",
        qualifiers=[],
        input_category="biolink:Disease",
        output_category=None,
        components=[ComponentEnum.ars],
        test_env=TestEnvEnum.ci,
    )


def test_performance_test_is_finished_in_radiator(mocker):
    """A performance test must reach a terminal status in the radiator."""
    mocker.patch(
        "test_harness.run.QueryRunner",
        return_value=MockQueryRunner(logger),
    )
    mocker.patch(
        "test_harness.run.run_performance_test",
        return_value=_perf_results("ars", {}),
    )

    reporter = _RecordingReporter(base_url="http://ir")
    run_tests(
        tests={"TestCase_1": _performance_test_case()},
        reporter=reporter,
        collector=MockResultCollector("ci", logger),
        logger=logger,
        args={"suite": "perf", "trapi_version": "1.6.0"},
    )

    assert reporter.finished, "performance test was never finished in the radiator"
    assert reporter.finished[0][1] == AgentStatus.PASSED.value


def test_performance_test_finished_failed_on_error(mocker):
    """If the performance run raises, the radiator still gets a terminal
    FAILED status instead of a perpetually-unfinished test."""
    mocker.patch(
        "test_harness.run.QueryRunner",
        return_value=MockQueryRunner(logger),
    )
    mocker.patch(
        "test_harness.run.run_performance_test",
        side_effect=RuntimeError("boom"),
    )

    reporter = _RecordingReporter(base_url="http://ir")
    run_tests(
        tests={"TestCase_1": _performance_test_case()},
        reporter=reporter,
        collector=MockResultCollector("ci", logger),
        logger=logger,
        args={"suite": "perf", "trapi_version": "1.6.0"},
    )

    assert reporter.finished
    assert reporter.finished[0][1] == AgentStatus.FAILED.value


class _NoResponseQueryRunner(MockQueryRunner):
    """Simulates a skipped test case: no query responses come back for any
    asset (eg the ARS query never ran / query generation failed)."""

    def run_queries(self, test_case):
        return {}, {}


def test_skipped_test_case_marks_all_assets_and_agents_skipped(mocker):
    """When an acceptance test case is skipped, every asset must be finished
    as SKIPPED in the radiator and recorded as SKIPPED for every agent in the
    stats/CSV -- not FAILED for ARAs or NO_RESULTS for ARS, and never dropped
    from the per-agent stats entirely."""
    mocker.patch(
        "test_harness.run.QueryRunner",
        return_value=_NoResponseQueryRunner(logger),
    )

    collector = ResultCollector("ci", logger)
    reporter = _RecordingReporter(base_url="http://ir")
    run_tests(
        tests=example_test_cases,
        reporter=reporter,
        collector=collector,
        logger=logger,
        args={"suite": "acceptance", "trapi_version": "1.6.0"},
    )

    # 3 assets total across the two acceptance cases in the fixture.
    assert len(reporter.finished) == 3
    assert all(result == AgentStatus.SKIPPED.value for _, result in reporter.finished)
    assert collector.acceptance_report[AgentStatus.SKIPPED.value] == 3
    assert collector.acceptance_report[AgentStatus.FAILED.value] == 0
    assert collector.acceptance_report[AgentStatus.NO_RESULTS.value] == 0

    # Every asset shows up in the per-agent stats as SKIPPED (no ARA entry is
    # silently dropped, so "0 SKIPPED" can't happen).
    for agent in collector.agents:
        per_agent = collector.acceptance_stats[agent]
        skipped_total = sum(
            per_agent[query_type][AgentStatus.SKIPPED.value]
            for query_type in collector.query_types
        )
        assert skipped_total == 3, f"{agent} should have 3 SKIPPED"

    # CSV has a row per asset (plus header), all SKIPPED, and labels were
    # uploaded as SKIPPED for every agent on every asset.
    data_rows = collector.acceptance_csv.strip().splitlines()[1:]
    assert len(data_rows) == 3
    for row in data_rows:
        assert "FAILED" not in row and "NO_RESULTS" not in row
    assert len(reporter.labels) == 3
    for label_set in reporter.labels:
        assert {label["key"] for label in label_set} == set(collector.agents)
        assert all(label["value"] == AgentStatus.SKIPPED.value for label in label_set)
