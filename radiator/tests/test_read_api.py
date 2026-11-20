"""The open read API: what scripts and analysts pull results with."""

import csv
import io
import uuid
from datetime import datetime, timedelta, timezone

import radiator_schema as schema

from . import factories as f

T0 = datetime(2026, 10, 1, 6, 0, tzinfo=timezone.utc)


def _upload(api, run, assets, finished=True):
    assert (
        api.post("/api/ingest/runs", json=run.model_dump(mode="json")).status_code
        == 201
    )
    batch = schema.ResultBatch(results=assets)
    api.post(
        f"/api/ingest/runs/{run.run_id}/results", json=batch.model_dump(mode="json")
    )
    if finished:
        finish = f.finish(run.started_at, counts={"PASSED": len(assets)})
        api.patch(f"/api/ingest/runs/{run.run_id}", json=finish.model_dump(mode="json"))
    return run.run_id


def _assets():
    return [
        f.asset(
            "Asset_2",
            parent_pk="parent-2",
            status="FAILED",
            agents=[
                f.agent("ars", "FAILED", message="not found", pk="merged-2"),
                f.agent("aragorn", "NO_RESULTS", pk="child-2"),
            ],
        ),
        f.asset(
            "Asset_1",
            parent_pk="parent-1",
            agents=[
                f.agent("ars", found=True, rank=3, score=0.9, pk="merged-1"),
                f.agent("arax", found=True, rank=1, pk="child-1"),
            ],
        ),
    ]


def test_a_run_has_every_field(client, api):
    run_id = _upload(api, f.run_create(started_at=T0), _assets())
    data = client.get(f"/api/runs/{run_id}").json()
    asset = next(a for a in data["results"] if a["asset_id"] == "Asset_2")
    assert asset["parent_pk"] == "parent-2"
    assert asset["predicate"] == "biolink:treats"
    assert asset["details"] == {}
    ars = next(a for a in asset["agents"] if a["agent"] == "ars")
    assert (ars["message"], ars["pk"]) == ("not found", "merged-2")
    assert data["zebrunner_launch"] is None


def test_pks(client, api):
    run_id = _upload(api, f.run_create(started_at=T0), _assets())
    rows = client.get(f"/api/runs/{run_id}/pks").json()
    assert rows == [
        {
            "test_case_id": "TestCase_1",
            "asset_id": "Asset_1",
            "parent_pk": "parent-1",
            "agent": "ars",
            "pk": "merged-1",
        },
        {
            "test_case_id": "TestCase_1",
            "asset_id": "Asset_1",
            "parent_pk": "parent-1",
            "agent": "arax",
            "pk": "child-1",
        },
        {
            "test_case_id": "TestCase_1",
            "asset_id": "Asset_2",
            "parent_pk": "parent-2",
            "agent": "ars",
            "pk": "merged-2",
        },
        {
            "test_case_id": "TestCase_1",
            "asset_id": "Asset_2",
            "parent_pk": "parent-2",
            "agent": "aragorn",
            "pk": "child-2",
        },
    ]
    ars = client.get(f"/api/runs/{run_id}/pks", params={"agent": "ars"}).json()
    assert [r["pk"] for r in ars] == ["merged-1", "merged-2"]


def test_pks_as_csv(client, api):
    run_id = _upload(api, f.run_create(started_at=T0), _assets())
    res = client.get(f"/api/runs/{run_id}/pks", params={"format": "csv"})
    assert res.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(res.text)))
    assert rows[0] == {
        "test_case_id": "TestCase_1",
        "asset_id": "Asset_1",
        "parent_pk": "parent-1",
        "agent": "ars",
        "pk": "merged-1",
    }
    assert len(rows) == 4


def test_flat_results(client, api):
    run_id = _upload(api, f.run_create(started_at=T0), _assets())
    rows = client.get(f"/api/runs/{run_id}/results").json()
    assert len(rows) == 4
    first = rows[0]
    assert (first["asset_id"], first["agent"], first["rank"]) == ("Asset_1", "ars", 3)
    assert first["asset_status"] == "PASSED"
    assert first["output_curies"] == ["CHEBI:31690"]

    failed = client.get(
        f"/api/runs/{run_id}/results", params={"status": "FAILED"}
    ).json()
    assert [(r["asset_id"], r["agent"]) for r in failed] == [("Asset_2", "ars")]

    res = client.get(
        f"/api/runs/{run_id}/results", params={"format": "csv", "agent": "arax"}
    )
    rows = list(csv.DictReader(io.StringIO(res.text)))
    assert [(r["asset_id"], r["rank"], r["pk"]) for r in rows] == [
        ("Asset_1", "1", "child-1")
    ]


def test_an_asset_without_agents_still_has_a_row(client, api):
    run_id = _upload(
        api,
        f.run_create(started_at=T0),
        [f.asset("Asset_1", status="SKIPPED", agents=[], parent_pk="p")],
    )
    rows = client.get(f"/api/runs/{run_id}/results").json()
    assert [(r["asset_id"], r["agent"], r["asset_status"]) for r in rows] == [
        ("Asset_1", None, "SKIPPED")
    ]
    pks = client.get(f"/api/runs/{run_id}/pks").json()
    assert pks == [
        {
            "test_case_id": "TestCase_1",
            "asset_id": "Asset_1",
            "parent_pk": "p",
            "agent": None,
            "pk": None,
        }
    ]


def test_unknown_run(client):
    for what in ("", "/results", "/pks"):
        assert client.get(f"/api/runs/{uuid.uuid4()}{what}").status_code == 404


def test_listing_runs(client, api):
    old = _upload(api, f.run_create(started_at=T0), _assets())
    new = _upload(api, f.run_create(started_at=T0 + timedelta(days=2)), _assets())
    other = _upload(
        api, f.run_create(started_at=T0 + timedelta(days=1), env="test"), _assets()
    )

    def ids(**params):
        return [r["run_id"] for r in client.get("/api/runs", params=params).json()]

    assert ids() == [str(new), str(other), str(old)]
    assert ids(env="ci") == [str(new), str(old)]
    assert ids(since=(T0 + timedelta(hours=1)).isoformat()) == [str(new), str(other)]
    assert ids(until=(T0 + timedelta(hours=1)).isoformat()) == [str(old)]
    assert ids(limit=1, offset=1) == [str(other)]


def test_latest_run(client, api):
    _upload(api, f.run_create(started_at=T0), _assets())
    done = _upload(api, f.run_create(started_at=T0 + timedelta(days=1)), _assets())
    going = _upload(
        api, f.run_create(started_at=T0 + timedelta(days=2)), _assets(), finished=False
    )
    params = {"suite": "sprint_4_tests", "env": "ci"}
    latest = client.get("/api/runs/latest", params=params).json()
    assert latest["run_id"] == str(done)
    assert "results" not in latest
    with_results = client.get(
        "/api/runs/latest", params={**params, "results": "true"}
    ).json()
    assert len(with_results["results"]) == 2
    running = client.get(
        "/api/runs/latest", params={**params, "finished": "false"}
    ).json()
    assert running["run_id"] == str(going)
    missing = client.get("/api/runs/latest", params={"suite": "nope"})
    assert missing.status_code == 404


def test_finding_a_run_by_its_zebrunner_launch(app, client, api):
    run = f.run_create(started_at=T0, origin="zebrunner_import", origin_ref="4321")
    run_id = _upload(api, run, _assets())
    _upload(api, f.run_create(started_at=T0), _assets())
    found = client.get("/api/runs", params={"zebrunner_launch": "4321"}).json()
    assert [r["run_id"] for r in found] == [str(run_id)]
    assert found[0]["zebrunner_launch"] == "4321"
    assert client.get("/api/runs", params={"zebrunner_launch": "1"}).json() == []
