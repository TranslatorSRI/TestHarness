"""A run's TRAPI version, and a change of version between runs."""

import uuid
from datetime import datetime, timedelta, timezone

import radiator_schema as schema
from radiator import cycles, queries
from radiator.ingest import ingest_payload

from . import factories as f

T0 = datetime(2026, 9, 7, 6, tzinfo=timezone.utc)


def _run(app, started, trapi, status="PASSED", cycle_id=None):
    payload = schema.RunPayload(
        run=f.run_create(started_at=started, trapi_version=trapi, cycle_id=cycle_id),
        results=[f.asset("A1", status=status, agents=[f.agent("ars", status)])],
        finish=f.finish(started),
    )
    with app.state.sessionmaker() as session:
        ingest_payload(session, payload)
        session.commit()
    return payload.run.run_id


def test_trapi_change():
    class R:
        def __init__(self, v):
            self.trapi_version = v

    assert queries.trapi_change(R("1.6.0"), R("2.0.0")) == ("1.6", "2.0")
    assert queries.trapi_change(R("2.0.0"), R("2.0.1")) is None  # patch only
    assert queries.trapi_change(R(None), R("2.0.0")) is None  # unknown (imported)
    assert queries.trapi_change(None, R("2.0.0")) is None


def test_an_upgrade_is_called_out(app, api, browser):
    _run(app, T0, "1.6.0")
    cycle_id = uuid.uuid4()
    after = _run(
        app, T0 + timedelta(days=7), "2.0.0", status="FAILED", cycle_id=cycle_id
    )

    summary = api.get(f"/api/runs/{after}/summary").json()
    assert (summary["previous_trapi_version"], summary["trapi_version"]) == (
        "1.6.0",
        "2.0.0",
    )
    assert (
        api.get(f"/api/runs/{after}", params={"results": "false"}).json()[
            "trapi_version"
        ]
        == "2.0.0"
    )

    page = browser.get(f"/runs/{after}").text
    assert "TRAPI 2.0</span>" in page
    assert "<b>TRAPI 1.6 → 2.0</b> since the previous run" in page
    assert "TRAPI 1.6 → 2.0" in browser.get(f"/runs/{after}/diff").text
    assert "TRAPI 2.0</span>" in browser.get("/").text

    with app.state.sessionmaker() as session:
        row = cycles.get_cycle(session, cycle_id).rows[0]
    assert row.flags[0].kind == "trapi_change"
    assert "TRAPI 1.6 → 2.0 since the previous run" in row.flags[0].text
    # still compared: the regression shows, with the reason beside it
    assert row.regressions == 1


def test_the_same_version_says_nothing(app, browser):
    _run(app, T0, "2.0.0")
    after = _run(app, T0 + timedelta(days=7), "2.0.0")
    assert "since the previous run: some" not in browser.get(f"/runs/{after}").text
