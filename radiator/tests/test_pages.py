"""The dashboard's pages and the queries behind them."""

from datetime import datetime, timedelta, timezone

import radiator_schema as schema
from radiator import queries
from radiator.ingest import ingest_payload

from . import factories as f

T0 = datetime(2026, 9, 1, 6, tzinfo=timezone.utc)


def _load(app, *payloads):
    with app.state.sessionmaker() as session:
        for payload in payloads:
            ingest_payload(session, payload)
        session.commit()


def _payload(started_at, results, **run_kwargs):
    run = f.run_create(started_at=started_at, **run_kwargs)
    return schema.RunPayload(run=run, results=results, finish=f.finish(started_at))


def test_pages_render(app, browser):
    perf = schema.RunPayload(
        run=f.run_create(started_at=T0, suite="performance_tests"),
        performance=[
            schema.PerformanceResult(
                test_case_id="P",
                asset_id="A",
                host="https://ars.ci.transltr.io",
                helmsdeep_target="ars",
                status="PASSED",
                max_sustainable_concurrency=12.0,
            )
        ],
    )
    first = _payload(T0, [f.asset("Asset_1", agents=[f.agent("ars", rank=4)])])
    second = _payload(
        T0 + timedelta(days=1),
        [
            f.asset(
                "Asset_1", status="FAILED", agents=[f.agent("ars", "FAILED", rank=90)]
            )
        ],
    )
    _load(app, perf, first, second)
    run_id = second.run.run_id

    runs = browser.get("/")
    assert runs.status_code == 200
    assert "sprint_4_tests" in runs.text and "performance_tests" in runs.text

    page = browser.get(f"/runs/{run_id}")
    assert page.status_code == 200
    assert "Asset_1 name" in page.text
    assert 'href="/runs/{}/diff#regression">1<'.format(run_id) in page.text

    diff = browser.get(f"/runs/{run_id}/diff")
    assert diff.status_code == 200
    assert "4 → 90" in diff.text

    asset = browser.get("/assets/TestCase_1/Asset_1?env=ci")
    assert asset.status_code == 200
    # a run without an env links with env= empty: still one env's history
    assert "last 2 runs in ci" in browser.get("/assets/TestCase_1/Asset_1?env=").text
    assert "Rank of the expected answer" in asset.text

    assert browser.get("/trends").status_code == 200
    performance = browser.get("/performance")
    assert performance.status_code == 200
    assert "https://ars.ci.transltr.io (ars)" in performance.text


def test_empty_database(browser):
    assert "No runs yet" in browser.get("/").text
    assert browser.get("/trends").status_code == 200
    assert browser.get("/performance").status_code == 200


def test_missing_things_404(browser):
    assert browser.get("/runs/6e3c1d8e-5f0e-4bb8-9d7e-1f1e0b6f0a11").status_code == 404
    assert browser.get("/assets/nope/nope").status_code == 404


def test_uploaded_text_is_escaped(app, browser):
    """Names and messages come from uploads; they must never become markup."""
    evil = '<script>alert("x")</script>'
    payload = _payload(
        T0,
        [
            f.asset(
                "Asset_1", name=evil, agents=[f.agent("ars", "FAILED", message=evil)]
            )
        ],
    )
    _load(app, payload)
    page = browser.get(f"/runs/{payload.run.run_id}").text
    assert evil not in page
    assert "&lt;script&gt;" in page


def test_matrix_filters(app, browser):
    payload = _payload(
        T0,
        [
            f.asset("Asset_1", agents=[f.agent("ars"), f.agent("arax", "FAILED")]),
            f.asset("Asset_2", status="FAILED", agents=[f.agent("ars", "FAILED")]),
        ],
    )
    _load(app, payload)
    url = f"/runs/{payload.run.run_id}"
    failed_overall = browser.get(url, params={"status": "FAILED"}).text
    assert "Asset_2 name" in failed_overall and "Asset_1 name" not in failed_overall
    failed_arax = browser.get(url, params={"status": "FAILED", "agent": "arax"}).text
    assert "Asset_1 name" in failed_arax and "Asset_2 name" not in failed_arax
    search = browser.get(url, params={"q": "asset_2"}).text
    assert "Asset_2 name" in search and "Asset_1 name" not in search


def _run(app, payload):
    with app.state.sessionmaker() as session:
        return queries.get_run(session, payload.run.run_id)


def test_diff_classification(app):
    before = _payload(
        T0,
        [
            f.asset("regressed", agents=[f.agent("ars", rank=3)]),
            f.asset("fixed", agents=[f.agent("ars", "FAILED")]),
            f.asset("changed", agents=[f.agent("ars", "FAILED")]),
            f.asset("skipped", agents=[f.agent("ars")]),
            f.asset("moved", agents=[f.agent("ars", rank=40)]),
            f.asset("jittered", agents=[f.agent("ars", rank=10)]),
            f.asset("dropped"),
        ],
    )
    after = _payload(
        T0 + timedelta(days=1),
        [
            f.asset("regressed", agents=[f.agent("ars", "FAILED", rank=80)]),
            f.asset("fixed", agents=[f.agent("ars")]),
            f.asset("changed", agents=[f.agent("ars", "NO_RESULTS")]),
            f.asset("skipped", agents=[f.agent("ars", "SKIPPED")]),
            f.asset("moved", agents=[f.agent("ars", rank=4)]),
            f.asset("jittered", agents=[f.agent("ars", rank=12)]),
            f.asset("new"),
        ],
    )
    _load(app, before, after)
    diff = queries.diff_runs(_run(app, before), _run(app, after))
    found = {
        kind: [c.asset.asset_id for c in changes]
        for kind, changes in diff.changes.items()
    }
    assert found == {
        "regression": ["regressed"],
        "fixed": ["fixed"],
        "changed": ["changed"],
        "skipped": ["skipped"],
        # a 2-place wobble isn't a move
        "rank": ["moved"],
        "added": ["new"],
        "removed": ["dropped"],
    }
    [moved] = diff.changes["rank"]
    assert moved.rank_delta == 36


def test_previous_run_stays_in_its_series(app):
    """A run with a target override or another query type isn't the
    'previous run' of a full run, and doesn't show up in its trends."""
    full_1 = _payload(T0, [f.asset()])
    override = _payload(
        T0 + timedelta(hours=1),
        [f.asset()],
        target="aragorn",
        target_url="http://localhost:8080",
    )
    mvp1 = _payload(T0 + timedelta(hours=2), [f.asset()], query_type="MVP1")
    other_env = _payload(T0 + timedelta(hours=3), [f.asset()], env="test")
    full_2 = _payload(T0 + timedelta(hours=4), [f.asset()])
    _load(app, full_1, override, mvp1, other_env, full_2)

    with app.state.sessionmaker() as session:
        latest = queries.get_run(session, full_2.run.run_id)
        assert queries.previous_run(session, latest).id == full_1.run.run_id
        assert (
            queries.previous_run(session, queries.get_run(session, full_1.run.run_id))
            is None
        )
        points = queries.agent_trends(session, "sprint_4_tests", "ci", days=100000)
        assert [p.run.id for p in points] == [full_1.run.run_id, full_2.run.run_id]


def test_pass_rate_leaves_out_skips():
    assert queries.pass_rate({"PASSED": 3, "FAILED": 1, "SKIPPED": 10}) == 0.75
    assert queries.pass_rate({"SKIPPED": 4}) is None


def test_an_unfinished_run_is_not_a_baseline(app):
    finished = _payload(T0, [f.asset()])
    crashed = schema.RunPayload(
        run=f.run_create(started_at=T0 + timedelta(hours=1)), results=[f.asset()]
    )
    latest = _payload(T0 + timedelta(hours=2), [f.asset()])
    _load(app, finished, crashed, latest)
    with app.state.sessionmaker() as session:
        run = queries.get_run(session, latest.run.run_id)
        assert queries.previous_run(session, run).id == finished.run.run_id
        trend_runs = [
            p.run.id
            for p in queries.agent_trends(session, "sprint_4_tests", "ci", 100000)
        ]
        assert crashed.run.run_id not in trend_runs


def test_asset_page_environments(app, browser):
    """The default is the env with the most history; runs with no env have
    their own history (eg imported ones)."""
    _load(
        app,
        _payload(T0, [f.asset()], env="test"),
        _payload(T0 + timedelta(hours=1), [f.asset()], env="test"),
        _payload(T0 + timedelta(hours=2), [f.asset()], env="prod"),
        _payload(T0 + timedelta(hours=3), [f.asset()], env=None),
    )
    assert "last 2 runs in test" in browser.get("/assets/TestCase_1/Asset_1").text
    no_env = browser.get("/assets/TestCase_1/Asset_1?env=-").text
    assert "last 1 runs with no recorded environment" in no_env


def test_run_summary_and_history_png(app, api, client):
    first = _payload(
        T0,
        [
            f.asset("A1"),
            f.asset("A2", status="FAILED", agents=[f.agent("ars", "FAILED")]),
        ],
    )
    second = _payload(
        T0 + timedelta(days=1),
        [
            f.asset("A1", status="FAILED", agents=[f.agent("ars", "FAILED")]),
            f.asset("A2"),
        ],
    )
    other_series = _payload(T0 + timedelta(hours=12), [f.asset("A1")], env="test")
    later = _payload(T0 + timedelta(days=2), [f.asset("A1")])
    _load(app, first, second, other_series, later)
    run_id = second.run.run_id

    summary = api.get(f"/api/runs/{run_id}/summary").json()
    assert summary["pass_rate"] == 0.5
    assert summary["previous_run_id"] == str(first.run.run_id)
    assert summary["previous_pass_rate"] == 0.5
    assert (summary["regressions"], summary["fixed"]) == (1, 1)

    res = api.get(f"/api/runs/{run_id}/history.png")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/png"
    assert res.content.startswith(b"\x89PNG")
    assert client.get(f"/api/runs/{run_id}/history.png").status_code == 401

    with app.state.sessionmaker() as session:
        run = queries.get_run(session, run_id)
        history = queries.series_history(session, run)
        # same series only, and nothing after this run
        assert [r.id for r, _ in history] == [first.run.run_id, run_id]
        assert history[-1][1]["ars"] == {"FAILED": 1, "PASSED": 1}


def test_summary_of_a_first_run(app, api):
    only = _payload(T0, [f.asset()])
    _load(app, only)
    summary = api.get(f"/api/runs/{only.run.run_id}/summary").json()
    assert summary["pass_rate"] == 1.0
    assert summary["previous_run_id"] is None and summary["regressions"] is None
    assert api.get(f"/api/runs/{only.run.run_id}/history.png").content.startswith(
        b"\x89PNG"
    )
