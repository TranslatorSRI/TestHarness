"""Regression tests for test-result propagation to reporting services.

These cover bugs where results were dropped or mangled on their way to the
Information Radiator (Reporter) and/or Slack (via the ResultCollector output):

* A skipped test case left its assets marked FAILED (for ARAs) / NO_RESULTS
  (for ARS) instead of SKIPPED, and assets that never got a query were dropped
  from the per-agent stats entirely.
* Performance tests were created in the radiator but never finished.
* A failed performance checkpoint, or a HelmsDeep run that never produced a
  summary at all, has to reach Slack as a FAIL rather than reading as a pass.
* Agents that returned no response were written to the CSV but omitted from
  the per-agent JSON stats.
* A failed ARS query submission (eg a 502) skipped the test for every agent
  instead of erroring the ARS and skipping only the ARAs.
"""

import json

import pytest
from translator_testing_model.datamodel.pydanticmodel import (
    ComponentEnum,
    PerformanceTestCase,
    TestEnvEnum,
    TestObjectiveEnum,
)

from test_harness.acceptance_test_runner import run_acceptance_pass_fail_analysis
from test_harness.performance_test_runner import (
    PROFILES,
    helmsdeep_target,
    resolve_profile,
)
from test_harness.result_collector import ResultCollector
from test_harness.run import run_tests
from test_harness.runner.generate_query import generate_query
from test_harness.utils import AgentReport, AgentStatus, TestReport, hash_test_asset

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


def _ara_result(curie, score):
    """An ARA-shaped result: scored by its own analyses, ranked by position."""
    return {
        "node_bindings": {"n1": [{"id": curie}]},
        "analyses": [{"score": score}],
    }


def _ars_result(curie, confidence, sugeno=0.5, rank=1):
    """An ARS-shaped result: scored by the ARS with a confidence (plus the
    sugeno score and rank, which shouldn't be used)."""
    return {
        "node_bindings": {"n1": [{"id": curie}]},
        "analyses": [{"score": 0.1}],
        "sugeno": sugeno,
        "rank": rank,
        "ordering_components": {"confidence": confidence},
    }


def test_csv_reports_expected_answer_rank_and_score():
    """The CSV must say whether the expected answer was in each agent's
    response, and at what rank/score, using the same numbers the report JSON
    uploaded to the radiator carries."""
    collector = ResultCollector("dev", logger)
    report = TestReport(pks={}, result={}, test_details=None)
    # ARS found it 2nd by confidence; an ARA found it 3rd with its own
    # analysis score; another ARA didn't return it at all.
    report.result["ars"] = AgentReport(AgentStatus.SKIPPED, None, None)
    run_acceptance_pass_fail_analysis(
        report.result,
        "ars",
        [
            _ars_result("MONDO:1", 0.9),
            _ars_result("CHEBI:2", 0.8),
        ],
        "CHEBI:2",
        "TopAnswer",
    )
    report.result["shepherd-aragorn"] = AgentReport(AgentStatus.SKIPPED, None, None)
    run_acceptance_pass_fail_analysis(
        report.result,
        "shepherd-aragorn",
        [
            _ara_result("MONDO:1", 0.7),
            _ara_result("MONDO:3", 0.6),
            _ara_result("CHEBI:2", 0.5),
        ],
        "CHEBI:2",
        "TopAnswer",
    )
    report.result["shepherd-arax"] = AgentReport(AgentStatus.SKIPPED, None, None)
    run_acceptance_pass_fail_analysis(
        report.result,
        "shepherd-arax",
        [_ara_result("MONDO:1", 0.7)],
        "CHEBI:2",
        "TopAnswer",
    )

    collector.collect_acceptance_result(_Case(), _Asset(), report, "pk", "http://ir/1")

    header = collector.acceptance_csv.splitlines()[0].split(",")
    row = dict(
        zip(header, collector.acceptance_csv.strip().splitlines()[-1].split(","))
    )
    for agent in collector.agents:
        for suffix in ("_found", "_rank", "_score"):
            assert f"{agent}{suffix}" in header

    # ARS: found, with the confidence rank/score.
    assert row["ars"] == AgentStatus.PASSED.value
    assert row["ars_found"] == "true"
    assert row["ars_rank"] == "2"
    assert row["ars_score"] == "0.8"
    # ARA: found, ranked by its position in the result list.
    assert row["shepherd-aragorn_found"] == "true"
    assert row["shepherd-aragorn_rank"] == "3"
    assert row["shepherd-aragorn_score"] == "0.5"
    # ARA that never returned the expected answer: found is false, and there
    # is no rank/score to report.
    assert row["shepherd-arax_found"] == "false"
    assert row["shepherd-arax_rank"] == ""
    assert row["shepherd-arax_score"] == ""
    # Agent that didn't respond at all: the question has no answer.
    assert row["shepherd-bte"] == AgentStatus.SKIPPED.value
    assert row["shepherd-bte_found"] == ""


def test_csv_expected_answer_columns_blank_when_no_analysis():
    """Skipped/errored agents get blank detail columns; an agent that came
    back with no results at all definitively didn't have the answer."""
    collector = ResultCollector("dev", logger)
    report = TestReport(
        pks={},
        result={
            "ars": AgentReport(
                status=AgentStatus.NO_RESULTS, message="No results", actual_output=None
            ),
            "shepherd-aragorn": AgentReport(
                status=AgentStatus.FAILED, message="Test Error", actual_output=None
            ),
        },
        test_details=None,
    )
    collector.collect_acceptance_result(_Case(), _Asset(), report, "pk", "http://ir/1")

    header = collector.acceptance_csv.splitlines()[0].split(",")
    row = dict(
        zip(header, collector.acceptance_csv.strip().splitlines()[-1].split(","))
    )
    assert row["ars_found"] == "false"
    assert row["ars_rank"] == "" and row["ars_score"] == ""
    assert row["shepherd-aragorn_found"] == ""
    assert row["shepherd-aragorn_rank"] == ""


def test_ars_results_ranked_by_confidence():
    """ARS results are scored by confidence (not sugeno) and ranked by their
    position once sorted by it, even if they come back out of order."""
    results = [
        _ars_result("MONDO:1", 0.2, sugeno=0.9, rank=1),
        _ars_result("MONDO:3", 0.5, sugeno=0.8, rank=2),
        _ars_result("CHEBI:2", 0.9, sugeno=0.1, rank=3),
    ]
    report = {"ars": AgentReport(AgentStatus.SKIPPED, None, None)}
    run_acceptance_pass_fail_analysis(report, "ars", results, "CHEBI:2", "Acceptable")
    # Top 50% after sorting is just CHEBI:2, so it passes.
    assert report["ars"].status == AgentStatus.PASSED
    assert report["ars"].actual_output["ars_rank"] == 1
    assert report["ars"].actual_output["ars_score"] == 0.9

    report = {"ars": AgentReport(AgentStatus.SKIPPED, None, None)}
    run_acceptance_pass_fail_analysis(report, "ars", results, "MONDO:1", "Acceptable")
    assert report["ars"].status == AgentStatus.FAILED
    assert report["ars"].actual_output["ars_rank"] == 3
    assert report["ars"].actual_output["ars_score"] == 0.2


def test_analysis_records_expected_answer_found_flag():
    """The analysis records whether the expected answer was in the response,
    even when the test passes because it was correctly absent."""
    report = {"ars": AgentReport(AgentStatus.SKIPPED, None, None)}
    run_acceptance_pass_fail_analysis(
        report,
        "ars",
        [_ara_result("MONDO:1", 0.7)],
        "CHEBI:2",
        "NeverShow",
    )
    assert report["ars"].status == AgentStatus.PASSED
    assert report["ars"].actual_output["found"] is False

    report = {"ars": AgentReport(AgentStatus.SKIPPED, None, None)}
    run_acceptance_pass_fail_analysis(
        report,
        "ars",
        [_ara_result("CHEBI:2", 0.7)],
        "CHEBI:2",
        "NeverShow",
    )
    assert report["ars"].status == AgentStatus.FAILED
    assert report["ars"].actual_output["found"] is True
    assert report["ars"].actual_output["ara_rank"] == 1


def _helmsdeep_summary(checkpoints=None, concurrency=42.5):
    """A minimal HelmsDeep summary.json, shaped like the real thing."""
    summary = {
        "config": {
            "target": "aras_mixed",
            "time_scale": 1.0,
            "component": "Shepherd (Mixed 2:1 inferred/Pathfinder)",
            "endpoint": "/query",
            "protocol": "sync",
            "p99_slo_ms": 300000,
            "max_error_rate": 0.01,
        },
        "stages": [],
        "knee": {
            "stage": 1,
            "users": 30,
            "p99_ms": 120000.0,
            "error_rate": 0.0,
            "rps": 2.5,
            "concurrency": concurrency,
        },
        "max_sustainable_concurrency": concurrency,
        "stage_warnings": [],
        "knee_unsupported": False,
    }
    if checkpoints is not None:
        summary["checkpoints"] = checkpoints
        summary["checkpoints_passed"] = all(c["verdict"] == "PASS" for c in checkpoints)
    return summary


def _checkpoint(users, verdict, goal="sustain peak load"):
    return {
        "users": users,
        "goal": goal,
        "p99_slo_ms": 300000,
        "max_error_rate": 0.01,
        "stage": 1,
        "requests": 500,
        "p99_ms": 120000.0 if verdict == "PASS" else 400000.0,
        "error_rate": 0.0 if verdict == "PASS" else 0.08,
        "concurrency": 29.4,
        "verdict": verdict,
        "detail": "within limits" if verdict == "PASS" else "p99 400000ms > 300000ms",
    }


def _perf_results(summary, error=None, target="aras_mixed", exit_code=0):
    """What the HelmsDeep driver hands the collector."""
    return {
        "runner": "helmsdeep",
        "helmsdeep_target": target,
        "component": "aragorn",
        "profile": "mixed",
        "host": "http://ara",
        "prefix": "test_results/run",
        "exit_code": exit_code,
        "summary": summary,
        "report_html": "<html>report</html>",
        "artifacts": {},
        "error": error,
        "output": "",
    }


def test_performance_checkpoint_failure_reaches_the_summary():
    """A missed checkpoint must be the pass/fail Slack shows."""
    collector = ResultCollector("prod", logger)
    collector.collect_performance_result(
        _Case(),
        _Asset(),
        "http://ir/1",
        "http://ara",
        _perf_results(
            _helmsdeep_summary(
                checkpoints=[
                    _checkpoint(30, "PASS"),
                    _checkpoint(45, "FAIL", "headroom above peak"),
                ]
            ),
            exit_code=1,
        ),
    )
    assert collector.performance_checkpoints_passed is False
    summary = collector.dump_result_summary()
    assert "checkpoints: *FAIL*" in summary
    assert "45 users - headroom above peak: FAIL" in summary
    assert "Max sustainable concurrency: 42.5" in summary


def test_performance_checkpoints_pass():
    collector = ResultCollector("prod", logger)
    collector.collect_performance_result(
        _Case(),
        _Asset(),
        "http://ir/1",
        "http://ara",
        _perf_results(_helmsdeep_summary(checkpoints=[_checkpoint(30, "PASS")])),
    )
    assert collector.performance_checkpoints_passed is True
    assert "checkpoints: *PASS*" in collector.dump_result_summary()


def test_performance_run_without_checkpoints_has_no_verdict():
    """A knee-finding run has nothing to pass or fail; don't claim a pass."""
    collector = ResultCollector("prod", logger)
    collector.collect_performance_result(
        _Case(),
        _Asset(),
        "http://ir/1",
        "http://ara",
        _perf_results(_helmsdeep_summary(), target="aras"),
    )
    assert collector.performance_checkpoints_passed is None
    summary = collector.dump_result_summary()
    assert "no checkpoints configured" in summary
    assert "Checkpoints: none configured for this run type" in summary


def test_performance_run_that_never_completed_counts_as_a_failure():
    """No summary.json means the run broke -- it must not read as a pass."""
    collector = ResultCollector("prod", logger)
    collector.collect_performance_result(
        _Case(),
        _Asset(),
        "http://ir/1",
        "http://ara",
        _perf_results(None, error="HelmsDeep exited 2 without writing a summary"),
    )
    assert collector.performance_checkpoints_passed is False
    assert "RUN FAILED" in collector.dump_result_summary()
    assert collector.performance_report["failures"]


def test_performance_artifacts_are_summary_json_and_report_html():
    """Both deliverables are uploaded, each carrying the checkpoint verdict."""
    collector = ResultCollector("prod", logger)
    collector.collect_performance_result(
        _Case(),
        _Asset(),
        "http://ir/1",
        "http://ara.example.org",
        _perf_results(
            _helmsdeep_summary(checkpoints=[_checkpoint(30, "FAIL")]),
            exit_code=1,
        ),
    )
    artifacts = list(collector.render_performance_artifacts())
    names = [name for name, _content, _comment in artifacts]
    assert names == [
        "ara.example.org_aras_mixed_summary.json",
        "ara.example.org_aras_mixed_report.html",
    ]
    # The uploaded JSON is HelmsDeep's summary verbatim, not a reformatting.
    assert json.loads(artifacts[0][1].decode()) == _helmsdeep_summary(
        checkpoints=[_checkpoint(30, "FAIL")]
    )
    for _name, _content, comment in artifacts:
        assert "checkpoints FAIL (0/1 passed)" in comment


def test_helmsdeep_target_mapping():
    """Only the ARS speaks the async protocol; everything else is an ARA."""
    assert helmsdeep_target("ars") == "ars"
    assert helmsdeep_target("infores:ars", "mixed") == "ars_mixed"
    assert helmsdeep_target("aragorn") == "aras"
    assert helmsdeep_target("arax", "pathfinder") == "aras_pathfinder"
    # Every run type the harness can ask for must actually exist upstream.
    from helmsdeep import config as helmsdeep_config

    for component in ("ars", "aragorn"):
        for profile in PROFILES:
            assert helmsdeep_target(component, profile) in helmsdeep_config.TARGETS


def test_profile_comes_from_test_runner_settings_then_the_override():
    case = _performance_test_case()
    assert resolve_profile(case) == "default"

    case.test_runner_settings = ["inferred", "mixed"]
    assert resolve_profile(case) == "mixed"
    # An explicit --performance_profile wins over the test case.
    assert resolve_profile(case, "pathfinder") == "pathfinder"

    with pytest.raises(ValueError):
        resolve_profile(case, "nonsense")


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
        return_value=_perf_results(
            _helmsdeep_summary(checkpoints=[_checkpoint(30, "PASS")]), target="ars"
        ),
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


def test_performance_checkpoint_failure_fails_the_radiator_test(mocker):
    """A missed checkpoint is a measured failure, so the radiator agrees with
    Slack instead of showing the run as PASSED."""
    mocker.patch(
        "test_harness.run.QueryRunner",
        return_value=MockQueryRunner(logger),
    )
    mocker.patch(
        "test_harness.run.run_performance_test",
        return_value=_perf_results(
            _helmsdeep_summary(checkpoints=[_checkpoint(30, "FAIL")]),
            target="ars",
            exit_code=1,
        ),
    )

    reporter = _RecordingReporter(base_url="http://ir")
    run_tests(
        tests={"TestCase_1": _performance_test_case()},
        reporter=reporter,
        collector=MockResultCollector("ci", logger),
        logger=logger,
        args={"suite": "perf", "trapi_version": "1.6.0"},
    )

    assert reporter.finished[0][1] == AgentStatus.FAILED.value


def test_performance_run_without_a_summary_fails_the_radiator_test(mocker):
    """HelmsDeep exiting without a summary must not be reported as PASSED."""
    mocker.patch(
        "test_harness.run.QueryRunner",
        return_value=MockQueryRunner(logger),
    )
    mocker.patch(
        "test_harness.run.run_performance_test",
        return_value=_perf_results(
            None, error="HelmsDeep exited 2 without writing a summary", target="ars"
        ),
    )

    reporter = _RecordingReporter(base_url="http://ir")
    run_tests(
        tests={"TestCase_1": _performance_test_case()},
        reporter=reporter,
        collector=MockResultCollector("ci", logger),
        logger=logger,
        args={"suite": "perf", "trapi_version": "1.6.0"},
    )

    assert reporter.finished[0][1] == AgentStatus.FAILED.value


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


class _IdentityCuries(dict):
    def __missing__(self, curie):
        return curie


class _ArsBadGatewayQueryRunner(MockQueryRunner):
    """Sends every asset's query to an ARS whose submit endpoint 502s."""

    def run_queries(self, test_case):
        queries = {}
        for asset in test_case.test_assets:
            queries[hash_test_asset(asset)] = {
                "query": generate_query(asset),
                "responses": {},
                "pks": {},
            }
        self._send_queries([{"url": "http://ars", "infores": "infores:ars"}], queries)
        return queries, _IdentityCuries()


def test_ars_submission_failure_errors_ars_and_skips_aras(mocker, httpx_mock):
    """A 502 on ARS query submission is an ARS error: the test and the ARS are
    marked ERROR, while the ARAs (which never saw the query) are SKIPPED."""
    httpx_mock.add_response(url="http://ars/ars/api/submit", status_code=502)
    mocker.patch(
        "test_harness.run.QueryRunner",
        return_value=_ArsBadGatewayQueryRunner(logger),
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

    # Nothing was polled after the failed submit.
    assert all(
        request.url == "http://ars/ars/api/submit"
        for request in httpx_mock.get_requests()
    )

    assert len(reporter.finished) == 3
    assert all(result == AgentStatus.ERROR.value for _, result in reporter.finished)
    assert collector.acceptance_report[AgentStatus.ERROR.value] == 3
    assert collector.acceptance_report[AgentStatus.SKIPPED.value] == 0

    for agent in collector.agents:
        expected = AgentStatus.ERROR if agent == "ars" else AgentStatus.SKIPPED
        total = sum(
            collector.acceptance_stats[agent][query_type][expected.value]
            for query_type in collector.query_types
        )
        assert total == 3, f"{agent} should have 3 {expected.value}"

    assert len(reporter.labels) == 3
    for label_set in reporter.labels:
        labels = {label["key"]: label["value"] for label in label_set}
        assert set(labels) == set(collector.agents)
        for agent, value in labels.items():
            expected = AgentStatus.ERROR if agent == "ars" else AgentStatus.SKIPPED
            assert value == expected.value
