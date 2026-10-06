"""The ingest API, including against the harness's own client."""

import uuid

from fastapi.testclient import TestClient

import radiator_schema as schema
from radiator import queries
from test_harness.radiator_client import RadiatorClient, push

from . import factories as f
from .conftest import TOKEN


def _get(app, run_id):
    with app.state.sessionmaker() as session:
        return queries.get_run(session, run_id)


def test_full_run(app, api):
    run = f.run_create()
    assert (
        api.post("/api/ingest/runs", json=run.model_dump(mode="json")).status_code
        == 201
    )
    batch = schema.ResultBatch(
        results=[
            f.asset(
                "Asset_1",
                agents=[
                    f.agent(
                        "ars", found=True, rank=3, score=0.9, n_results=120, pk="pk1"
                    ),
                    f.agent("aragorn", "NO_RESULTS", n_results=0, http_status=200),
                ],
            )
        ],
        performance=[
            schema.PerformanceResult(
                test_case_id="Perf_1",
                asset_id="Asset_9",
                host="https://ars",
                status="PASSED",
                max_sustainable_concurrency=12.5,
                summary={"knee": {"users": 13}},
            )
        ],
    )
    res = api.post(
        f"/api/ingest/runs/{run.run_id}/results", json=batch.model_dump(mode="json")
    )
    assert res.json() == {"results": 1, "performance": 1}
    finish = f.finish(run.started_at, counts={"PASSED": 1})
    assert (
        api.patch(
            f"/api/ingest/runs/{run.run_id}", json=finish.model_dump(mode="json")
        ).status_code
        == 200
    )

    stored = _get(app, run.run_id)
    assert stored.ended_at == finish.ended_at
    assert stored.counts == {"PASSED": 1}
    [asset] = stored.assets
    ars, aragorn = asset.agents
    assert (ars.found, ars.rank, ars.score, ars.n_results, ars.pk) == (
        True,
        3,
        0.9,
        120,
        "pk1",
    )
    assert (aragorn.status, aragorn.n_results) == ("NO_RESULTS", 0)
    [perf] = stored.performance
    assert perf.max_sustainable_concurrency == 12.5
    assert perf.summary == {"knee": {"users": 13}}


def test_writes_are_idempotent(app, api):
    """Re-posting a run, or a result, updates it instead of duplicating it,
    and an asset's agent rows are replaced by the newest upload."""
    run = f.run_create()
    for _ in range(2):
        api.post("/api/ingest/runs", json=run.model_dump(mode="json"))
    first = schema.ResultBatch(
        results=[f.asset("Asset_1", agents=[f.agent("ars"), f.agent("arax")])]
    )
    second = schema.ResultBatch(
        results=[f.asset("Asset_1", status="FAILED", agents=[f.agent("ars", "FAILED")])]
    )
    for batch in (first, second):
        api.post(
            f"/api/ingest/runs/{run.run_id}/results", json=batch.model_dump(mode="json")
        )

    with app.state.sessionmaker() as session:
        assert len(queries.list_runs(session)) == 1
    [asset] = _get(app, run.run_id).assets
    assert asset.status == "FAILED"
    assert [(a.agent, a.status) for a in asset.agents] == [("ars", "FAILED")]


def test_results_for_an_unknown_run_are_refused(api):
    run_id = uuid.uuid4()
    res = api.post(f"/api/ingest/runs/{run_id}/results", json={"results": []})
    assert res.status_code == 404
    res = api.patch(
        f"/api/ingest/runs/{run_id}",
        json=f.finish().model_dump(mode="json"),
    )
    assert res.status_code == 404


def test_invalid_body_is_rejected(api):
    run = f.run_create()
    api.post("/api/ingest/runs", json=run.model_dump(mode="json"))
    res = api.post(
        f"/api/ingest/runs/{run.run_id}/results",
        json={
            "results": [
                {
                    "test_case_id": "x",
                    "asset_id": "y",
                    "kind": "acceptance",
                    "status": "MAYBE",
                }
            ]
        },
    )
    assert res.status_code == 422


def _harness_client(app, batch_size=2):
    """The harness's RadiatorClient, talking to this app in-process."""
    client = RadiatorClient(
        base_url="http://radiator", token=TOKEN, batch_size=batch_size
    )
    client._client = TestClient(
        app,
        base_url=f"http://radiator{schema.INGEST_PREFIX}",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    return client


def test_harness_client_round_trip(app):
    """The contract test: what the harness sends is what the radiator stores."""
    client = _harness_client(app)
    run = f.run_create()
    client.start_run(run)
    for i in range(5):
        client.add_result(f.asset(f"Asset_{i}"))
    client.finish_run(counts={"PASSED": 5})
    assert not client.upload_failed

    stored = _get(app, run.run_id)
    assert sorted(a.asset_id for a in stored.assets) == [f"Asset_{i}" for i in range(5)]
    assert stored.counts == {"PASSED": 5}


def test_replaying_a_partly_uploaded_run(app):
    """A run that half made it, replayed from the saved payload, ends up whole
    and without duplicates."""
    run = f.run_create()
    partial = _harness_client(app)
    partial.start_run(run)
    partial.add_result(f.asset("Asset_0"))
    partial.add_result(f.asset("Asset_1"))  # flushed: batch of 2

    payload = schema.RunPayload(
        run=run,
        results=[f.asset(f"Asset_{i}") for i in range(4)],
        finish=f.finish(run.started_at),
    )
    assert push(payload, _harness_client(app))

    stored = _get(app, run.run_id)
    assert len(stored.assets) == 4
    assert stored.ended_at == payload.finish.ended_at
