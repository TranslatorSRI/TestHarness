"""Tests for reporting to the new Information Radiator.

Covers the structured records the collector builds (including the response
metadata Zebrunner never got: n_results, HTTP status, response time), and the
client's batching, auth, failure handling, and save/replay.
"""

import json
from uuid import uuid4

from pytest_httpx import HTTPXMock

from radiator_schema import AssetResult, RunCreate, RunPayload
from test_harness.radiator_client import RadiatorClient, push
from test_harness.result_collector import ResultCollector
from test_harness.run import record_response_meta, run_tests
from test_harness.utils import AgentReport, AgentStatus, TestReport

from .helpers.example_tests import example_test_cases
from .helpers.logger import setup_logger
from .helpers.mock_responses import kp_response
from .helpers.mocks import MockReporter, MockResultCollector

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
