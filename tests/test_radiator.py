"""Tests for reporting to the new Information Radiator.

Covers the structured records the collector builds (including the response
metadata Zebrunner never got: n_results, HTTP status, response time), and the
client's batching, auth, failure handling, and save/replay.
"""

import json
import os
from uuid import uuid4

import pytest
from pytest_httpx import HTTPXMock

from radiator_schema import AssetResult, RunCreate, RunPayload
from test_harness.main import main
from test_harness.radiator_client import RadiatorClient, push
from test_harness.result_collector import ResultCollector
from test_harness.run import record_response_meta, run_tests
from test_harness.utils import AgentReport, AgentStatus, TestReport

from .helpers.example_tests import example_test_cases
from .helpers.logger import setup_logger
from .helpers.mock_responses import kp_response
from .helpers.mocks import MockReporter, MockResultCollector, MockSlacker

logger = setup_logger()

BASE = "http://radiator"
INGEST = f"{BASE}/api/ingest"


class _Asset:
    name = "asset-name"
    description = None
    id = "asset-1"
    expected_output = "TopAnswer"
    predicate_id = "biolink:treats"
    input_id = "MONDO:1"
    output_id = "CHEBI:2"


class _Case:
    id = "case-1"


def _run(**kwargs):
    return RunCreate(
        run_id=uuid4(), suite="suite", started_at="2026-10-06T00:00:00Z", **kwargs
    )


def _recording_client():
    """A client that records but sends nothing, with a run open."""
    client = RadiatorClient(enabled=False)
    client.start_run(_run())
    return client


def _report(status, **kwargs):
    return AgentReport(status=status, message=None, actual_output=None, **kwargs)


def test_record_response_meta_counts_results():
    report = _report(AgentStatus.SKIPPED)
    record_response_meta(
        report,
        {
            "response": {"message": {"results": [{}, {}, {}]}},
            "status_code": 200,
            "elapsed_s": 1.5,
        },
    )
    assert report.http_status == 200
    assert report.n_results == 3
    assert report.response_time_s == 1.5


def test_record_response_meta_ignores_stand_in_results_on_error():
    """An errored agent gets an empty stand-in result list from the query
    runner; that must not read as the agent answering with zero results."""
    report = _report(AgentStatus.SKIPPED)
    record_response_meta(
        report, {"response": {"message": {"results": []}}, "status_code": 598}
    )
    assert report.http_status == 598
    assert report.n_results is None
    assert report.response_time_s is None


def test_collector_builds_structured_asset_result():
    client = _recording_client()
    collector = ResultCollector("dev", logger, radiator=client)
    report = TestReport(
        pks={"parent_pk": "parent", "ars": "merged", "shepherd-arax": "arax-pk"},
        result={
            "ars": _report(AgentStatus.PASSED, http_status=200, n_results=120),
            "shepherd-arax": _report(
                AgentStatus.NO_RESULTS, http_status=200, n_results=0
            ),
        },
        test_details=None,
    )
    report.result["ars"].actual_output = {
        "found": True,
        "ars_rank": 4,
        "ars_score": 0.91,
    }
    collector.collect_acceptance_result(_Case(), _Asset(), report, "parent", "url")

    [result] = client.payload.results
    assert result.test_case_id == "case-1"
    assert result.asset_id == "asset-1"
    assert result.kind == "acceptance"
    assert result.input_curie == "MONDO:1"
    assert result.output_curie == "CHEBI:2"
    # derived from the ARS, as run_tests does
    assert result.status == "PASSED"
    assert result.parent_pk == "parent"

    agents = {agent.agent: agent for agent in result.agents}
    # one row per agent on the roster, like the CSV
    assert list(agents) == collector.agents
    ars = agents["ars"]
    assert (ars.found, ars.rank, ars.score) == (True, 4, 0.91)
    assert ars.n_results == 120
    assert ars.pk == "merged"
    arax = agents["shepherd-arax"]
    assert arax.status == "NO_RESULTS"
    assert arax.found is False
    assert arax.n_results == 0
    # no response at all: skipped, and nothing is claimed about it
    bte = agents["shepherd-bte"]
    assert bte.status == "SKIPPED"
    assert bte.found is None and bte.n_results is None


def test_collector_survives_a_record_the_schema_rejects():
    """A malformed value costs the radiator that asset, not the run."""
    client = _recording_client()
    collector = ResultCollector("dev", logger, radiator=client)
    report = TestReport(
        pks={},
        result={"ars": _report(AgentStatus.PASSED)},
        test_details=None,
    )
    report.result["ars"].actual_output = {"found": True, "ars_rank": "first"}
    collector.collect_acceptance_result(_Case(), _Asset(), report, None, "url")

    assert client.payload.results == []
    # the existing outputs are unaffected
    assert collector.acceptance_stats["ars"]["TopAnswer"]["PASSED"] == 1


def test_collector_force_skipped_skips_every_agent():
    client = _recording_client()
    collector = ResultCollector("dev", logger, radiator=client)
    report = TestReport(
        pks={"ars": "None"},
        result={"ars": _report(AgentStatus.FAILED, http_status=500)},
        test_details=None,
    )
    collector.collect_acceptance_result(
        _Case(), _Asset(), report, None, "url", force_skipped=True
    )

    [result] = client.payload.results
    assert result.status == "SKIPPED"
    assert {agent.status for agent in result.agents} == {"SKIPPED"}
    # the query runner's "None" placeholder is not a pk
    assert all(agent.pk is None for agent in result.agents)


def test_collector_records_failed_performance_checkpoint():
    client = _recording_client()
    collector = ResultCollector("ci", logger, radiator=client)
    collector.collect_performance_result(
        _Case(),
        _Asset(),
        "url",
        "https://ars.ci.transltr.io",
        {
            "helmsdeep_target": "ars",
            "exit_code": 0,
            "summary": {
                "checkpoints_passed": False,
                "max_sustainable_concurrency": 12.5,
            },
        },
    )

    [perf] = client.payload.performance
    assert perf.status == "FAILED"
    assert perf.checkpoints_passed is False
    assert perf.max_sustainable_concurrency == 12.5
    assert perf.summary["checkpoints_passed"] is False


def test_run_tests_records_response_metadata(httpx_mock: HTTPXMock):
    """End to end through run_tests: a direct query's result count and
    response time reach the radiator record."""
    httpx_mock.add_response(url="http://localhost:8080/query", json=kp_response)
    httpx_mock.add_response(
        url="https://nodenorm-es.ci.transltr.io/get_normalized_nodes",
        # nothing normalizes, so every curie is kept as is
        json={
            curie: None
            for curie in [
                "MONDO:0010794",
                "DRUGBANK:DB00313",
                "MESH:D001463",
                "CHEBI:18295",
                "CHEBI:31690",
                "CL:0000097",
                "MONDO:0004979",
                "NCBIGene:3815",
                "NCBIGene:4254",
                "PR:000049994",
            ]
        },
    )
    client = _recording_client()
    collector = MockResultCollector("ci", logger, target="aragorn", radiator=client)
    run_tests(
        tests=example_test_cases,
        reporter=MockReporter(base_url="http://test"),
        collector=collector,
        logger=logger,
        args={
            "suite": "testing",
            "trapi_version": "1.6.0",
            "target_url": "http://localhost:8080",
            "target": "aragorn",
        },
    )

    assert client.payload.results
    for result in client.payload.results:
        [aragorn] = result.agents
        assert aragorn.agent == "aragorn"
        assert aragorn.http_status == 200
        assert aragorn.n_results == len(kp_response["message"]["results"])
        assert aragorn.response_time_s is not None


def test_client_needs_url_and_token():
    assert not RadiatorClient.is_configured()
    assert not RadiatorClient.is_configured(base_url=BASE)
    assert RadiatorClient.is_configured(base_url=BASE, token="tok")
    assert not RadiatorClient(base_url=BASE).enabled
    assert not RadiatorClient(base_url=BASE, token="tok", enabled=False).enabled


def test_disabled_client_sends_nothing(httpx_mock: HTTPXMock):
    client = RadiatorClient(base_url=BASE, token="tok", enabled=False)
    client.start_run(_run())
    client.add_result(_asset_result())
    client.finish_run()
    assert httpx_mock.get_requests() == []
    assert client.run_url is None
    assert len(client.payload.results) == 1


def _asset_result(asset_id="asset-1"):
    return AssetResult(
        test_case_id="case-1", asset_id=asset_id, kind="acceptance", status="PASSED"
    )


def test_client_batches_results(httpx_mock: HTTPXMock):
    run = _run()
    httpx_mock.add_response(method="POST", url=f"{INGEST}/runs")
    httpx_mock.add_response(method="POST", url=f"{INGEST}/runs/{run.run_id}/results")
    httpx_mock.add_response(method="POST", url=f"{INGEST}/runs/{run.run_id}/results")
    httpx_mock.add_response(method="PATCH", url=f"{INGEST}/runs/{run.run_id}")

    client = RadiatorClient(base_url=BASE + "/", token="tok", batch_size=2)
    client.start_run(run)
    for i in range(3):
        client.add_result(_asset_result(f"asset-{i}"))
    client.finish_run(counts={"PASSED": 3})

    requests = httpx_mock.get_requests()
    assert [(r.method, r.url.path) for r in requests] == [
        ("POST", "/api/ingest/runs"),
        ("POST", f"/api/ingest/runs/{run.run_id}/results"),
        ("POST", f"/api/ingest/runs/{run.run_id}/results"),
        ("PATCH", f"/api/ingest/runs/{run.run_id}"),
    ]
    assert all(r.headers["Authorization"] == "Bearer tok" for r in requests)
    # a full batch of two, then the remainder on finish
    batches = [json.loads(r.content)["results"] for r in requests[1:3]]
    assert [len(batch) for batch in batches] == [2, 1]
    assert json.loads(requests[3].content)["counts"] == {"PASSED": 3}
    assert not client.upload_failed
    assert client.run_url == f"{BASE}/runs/{run.run_id}"


def test_client_failure_stops_uploads_and_replays(httpx_mock: HTTPXMock, tmp_path):
    """A failed upload never raises, stops further uploads, and the saved run
    replays in full, keeping its original end time."""
    run = _run()
    httpx_mock.add_response(method="POST", url=f"{INGEST}/runs", status_code=503)

    client = RadiatorClient(base_url=BASE, token="tok", batch_size=1)
    client.start_run(run)
    client.add_result(_asset_result("asset-1"))
    client.add_result(_asset_result("asset-2"))
    client.finish_run(counts={"PASSED": 2})

    assert client.upload_failed
    assert len(httpx_mock.get_requests()) == 1
    path = client.save(str(tmp_path), prefix="aragorn_")
    assert path.endswith(f"aragorn_radiator_{run.run_id}.json")

    with open(path) as f:
        payload = RunPayload.model_validate(json.load(f))
    assert len(payload.results) == 2

    httpx_mock.reset(assert_all_responses_were_requested=False)
    httpx_mock.add_response(method="POST", url=f"{INGEST}/runs")
    httpx_mock.add_response(method="POST", url=f"{INGEST}/runs/{run.run_id}/results")
    httpx_mock.add_response(method="PATCH", url=f"{INGEST}/runs/{run.run_id}")
    replay = RadiatorClient(base_url=BASE, token="tok")
    assert push(payload, replay)

    requests = httpx_mock.get_requests()
    assert len(json.loads(requests[1].content)["results"]) == 2
    finish = json.loads(requests[2].content)
    assert finish["ended_at"] == payload.finish.model_dump(mode="json")["ended_at"]
    assert finish["counts"] == {"PASSED": 2}


def test_record_response_meta_ignores_a_non_numeric_status():
    """After a failed ARS poll the status is the ARS's own string; that must
    not invalidate the asset's whole radiator record."""
    report = _report(AgentStatus.SKIPPED)
    record_response_meta(
        report, {"response": {"message": {"results": []}}, "status_code": "Running"}
    )
    assert report.http_status is None
    assert report.n_results is None


def test_collector_records_a_performance_run_that_raised():
    client = _recording_client()
    collector = ResultCollector("ci", logger, radiator=client)
    collector.record_performance_error(_Case(), _Asset(), "https://ars", "boom")
    [perf] = client.payload.performance
    assert (perf.status, perf.error, perf.host) == ("FAILED", "boom", "https://ars")


class _BrokenZebrunner(MockReporter):
    def create_test(self, test, asset):
        raise RuntimeError("Zebrunner is down")


def test_run_tests_still_collects_when_zebrunner_fails(httpx_mock: HTTPXMock):
    """A Zebrunner failure for an asset must not drop it from the radiator."""
    httpx_mock.add_response(url="http://localhost:8080/query", json=kp_response)
    httpx_mock.add_response(
        url="https://nodenorm-es.ci.transltr.io/get_normalized_nodes",
        json={
            curie: None
            for curie in [
                "MONDO:0010794",
                "DRUGBANK:DB00313",
                "MESH:D001463",
                "CHEBI:18295",
                "CHEBI:31690",
                "CL:0000097",
                "MONDO:0004979",
                "NCBIGene:3815",
                "NCBIGene:4254",
                "PR:000049994",
            ]
        },
    )
    client = _recording_client()
    collector = MockResultCollector("ci", logger, target="aragorn", radiator=client)
    run_tests(
        tests=example_test_cases,
        reporter=_BrokenZebrunner(base_url="http://test"),
        collector=collector,
        logger=logger,
        args={
            "suite": "testing",
            "trapi_version": "1.6.0",
            "target_url": "http://localhost:8080",
            "target": "aragorn",
        },
    )
    assert client.payload.results


def _main_args(tmp_path):
    return {
        "tests": example_test_cases,
        "suite": "testing",
        "save_to_dashboard": False,
        "json_output": False,
        "log_level": "ERROR",
        "output_dir": str(tmp_path),
    }


def _saved_payload(tmp_path):
    [path] = [p for p in os.listdir(tmp_path) if p.startswith("radiator_")]
    with open(os.path.join(tmp_path, path)) as f:
        return RunPayload.model_validate(json.load(f))


def test_main_survives_zebrunner_being_down(mocker, monkeypatch, tmp_path):
    """Zebrunner refusing to open a run no longer stops the run."""
    monkeypatch.setenv("ZE_BASE_URL", "http://zebrunner")
    monkeypatch.setenv("ZE_REFRESH_TOKEN", "tok")
    mocker.patch(
        "test_harness.main.Reporter.get_auth", side_effect=RuntimeError("down")
    )
    run = mocker.patch("test_harness.main.run_tests")
    assert main(_main_args(tmp_path)) == 0
    run.assert_called_once()
    assert _saved_payload(tmp_path).finish is not None


def test_main_saves_the_radiator_run_before_slack_can_fail(mocker, tmp_path):
    """The radiator run is closed and saved before Zebrunner and Slack are
    finished with, so their failures can't cost it the run."""
    mocker.patch("test_harness.main.run_tests")
    mocker.patch(
        "test_harness.main.LocalReporter.finish_test_run",
        side_effect=RuntimeError("Zebrunner said no"),
    )
    with pytest.raises(RuntimeError):
        main(_main_args(tmp_path))
    assert _saved_payload(tmp_path).finish is not None


def test_main_keeps_a_crashed_run_open(mocker, tmp_path):
    """A run that died partway is saved, but not marked finished."""
    mocker.patch("test_harness.main.run_tests", side_effect=RuntimeError("crash"))
    with pytest.raises(RuntimeError):
        main(_main_args(tmp_path))
    assert _saved_payload(tmp_path).finish is None


class _RecordingSlacker(MockSlacker):
    def __init__(self):
        self.messages = []
        self.files = []

    def post_notification(self, messages=[]):
        self.messages.extend(messages)

    def upload_binary_file(self, filename, content, initial_comment=None, title=None):
        self.files.append((filename, content, initial_comment))


def _main_with_slack(mocker, monkeypatch, tmp_path, *, radiator=True, summary=None):
    def fake_run_tests(tests, reporter, collector, logger, args):
        collector.has_acceptance_results = True

    mocker.patch("test_harness.main.run_tests", side_effect=fake_run_tests)
    slacker = _RecordingSlacker()
    mocker.patch("test_harness.main.Slacker", return_value=slacker)
    mocker.patch("test_harness.main.Slacker.is_configured", return_value=True)
    if radiator:
        monkeypatch.setenv("RADIATOR_URL", "http://radiator")
        monkeypatch.setenv("RADIATOR_TOKEN", "tok")
        mocker.patch("test_harness.main.RadiatorClient._send")
        mocker.patch("test_harness.main.RadiatorClient.summary", return_value=summary)
        mocker.patch(
            "test_harness.main.RadiatorClient.history_png", return_value=b"\x89PNG"
        )
    assert main(_main_args(tmp_path)) == 0
    return slacker


def test_slack_report_carries_the_radiator_history(mocker, monkeypatch, tmp_path):
    slacker = _main_with_slack(
        mocker,
        monkeypatch,
        tmp_path,
        summary={
            "pass_rate": 0.86,
            "previous_pass_rate": 0.9,
            "regressions": 3,
            "fixed": 1,
        },
    )
    report = slacker.messages[-1]
    assert "Pass rate 86% (-4.0 pts vs the previous run)" in report
    assert "3 regressions · 1 fixed" in report
    assert "/diff|What changed>" in report
    [(filename, content, comment)] = slacker.files
    assert filename == "pass_rate_history.png" and content == b"\x89PNG"
    assert "Open in the Information Radiator" in comment


def test_slack_report_first_run_of_a_series(mocker, monkeypatch, tmp_path):
    slacker = _main_with_slack(
        mocker,
        monkeypatch,
        tmp_path,
        summary={"pass_rate": 0.5, "previous_pass_rate": None},
    )
    assert "Pass rate 50%\n" in slacker.messages[-1]
    assert "What changed" not in slacker.messages[-1]


def test_slack_report_without_the_radiator(mocker, monkeypatch, tmp_path):
    """No radiator: the report is what it always was."""
    slacker = _main_with_slack(mocker, monkeypatch, tmp_path, radiator=False)
    assert "Pass rate" not in slacker.messages[-1]
    assert slacker.files == []
