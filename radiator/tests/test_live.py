"""Live updates: progress, what's running, and the pages that poll for it."""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import update

import radiator_schema as schema
from radiator.models import Run

from . import factories as f


def _open(api, expected_assets=3, **kw):
    run = f.run_create(expected_assets=expected_assets, **kw)
    assert (
        api.post("/api/ingest/runs", json=run.model_dump(mode="json")).status_code
        == 201
    )
    return run.run_id


def _upload(api, run_id, *asset_ids):
    batch = schema.ResultBatch(results=[f.asset(a) for a in asset_ids])
    res = api.post(
        f"/api/ingest/runs/{run_id}/results", json=batch.model_dump(mode="json")
    )
    assert res.status_code == 200


def _finish(api, run_id):
    finish = f.finish().model_dump(mode="json")
    assert api.patch(f"/api/ingest/runs/{run_id}", json=finish).status_code == 200


def test_progress_follows_a_run_until_it_finishes(api):
    run_id = _open(api)
    first = api.get(f"/api/runs/{run_id}/progress").json()
    assert first["running"] and first["state"] == "running"
    assert (first["done_assets"], first["expected_assets"]) == (0, 3)

    _upload(api, run_id, "A1", "A2")
    second = api.get(f"/api/runs/{run_id}/progress").json()
    assert second["done_assets"] == 2
    assert second["counts"] == {"PASSED": 2}
    assert second["version"] != first["version"]

    _finish(api, run_id)
    done = api.get(f"/api/runs/{run_id}/progress").json()
    assert not done["running"] and done["state"] == "finished"
    assert done["version"] != second["version"]


def test_a_run_that_stopped_uploading_did_not_finish(app, api):
    run_id = _open(api)
    with app.state.sessionmaker() as session:
        session.execute(
            update(Run)
            .where(Run.id == run_id)
            .values(updated_at=datetime.now(timezone.utc) - timedelta(hours=3))
        )
        session.commit()
    progress = api.get(f"/api/runs/{run_id}/progress").json()
    assert progress["state"] == "stalled" and not progress["running"]
    assert api.get("/api/live").json()["running"] == []


def test_live_status_changes_when_runs_start_upload_and_finish(api):
    empty = api.get("/api/live").json()
    run_id = _open(api)
    started = api.get("/api/live").json()
    assert started["running"] == [str(run_id)]
    assert started["version"] != empty["version"]
    _upload(api, run_id, "A1")
    uploaded = api.get("/api/live").json()
    assert uploaded["version"] != started["version"]
    _finish(api, run_id)
    finished = api.get("/api/live").json()
    assert finished["running"] == []
    assert finished["version"] != uploaded["version"]


def test_a_running_runs_page_polls_and_shows_progress(api, browser):
    run_id = _open(api)
    _upload(api, run_id, "A1")
    page = browser.get(f"/runs/{run_id}").text
    assert f'data-live="/api/runs/{run_id}/progress"' in page
    assert "data-live-until-done" in page
    assert "<b>1</b> of 3 assets" in page and 'aria-valuenow="1"' in page
    for region in (
        "run-meta",
        "run-progress",
        "run-tiles",
        "run-agents",
        "run-results",
    ):
        assert f'id="{region}" data-live-region' in page, region
    assert "running</span>" in page

    _finish(api, run_id)
    page = browser.get(f"/runs/{run_id}").text
    assert "data-live=" not in page
    assert "of 3 assets" not in page


def test_a_run_with_no_results_yet_says_so(api, browser):
    run_id = _open(api, expected_assets=None)
    page = browser.get(f"/runs/{run_id}").text
    assert "<b>0</b> assets so far" in page
    assert "No results yet" in page


def test_the_runs_list_and_weekly_page_poll(app, api, browser):
    running = _open(api)
    stalled = _open(api, started_at=datetime.now(timezone.utc) - timedelta(hours=5))
    with app.state.sessionmaker() as session:
        session.execute(
            update(Run)
            .where(Run.id == stalled)
            .values(updated_at=datetime.now(timezone.utc) - timedelta(hours=4))
        )
        session.commit()
    version = api.get("/api/live").json()["version"]
    runs = browser.get("/").text
    assert 'data-live="/api/live"' in runs
    assert f'data-live-version="{version}"' in runs
    assert 'id="runs-table" data-live-region' in runs
    assert runs.count("running</span>") == 1
    assert "didn&#39;t finish" in runs or "didn't finish" in runs
    weekly = browser.get("/weekly").text
    assert 'data-live="/api/live"' in weekly
    # a past week doesn't change, so it doesn't poll
    last_month = (datetime.now(timezone.utc) - timedelta(days=30)).date().isoformat()
    assert (
        "data-live=" not in browser.get("/weekly", params={"week_of": last_month}).text
    )
    assert running != stalled


def test_live_endpoints_need_a_login_or_token(client):
    assert client.get("/api/live").status_code == 401
    assert client.get(f"/api/runs/{uuid.uuid4()}/progress").status_code == 401


def test_what_changed_mid_run_leaves_out_untested_assets(api, browser):
    before = _open(api)
    _upload(api, before, "A1", "A2", "A3")
    _finish(api, before)
    run_id = _open(api)
    _upload(api, run_id, "A1")
    page = browser.get(f"/runs/{run_id}/diff").text
    assert "This run hasn&#39;t finished" in page or "This run hasn't finished" in page
    assert 'href="#removed"><span class="n">0</span>' in page
    assert "Pass rate so far" in browser.get(f"/runs/{run_id}").text
