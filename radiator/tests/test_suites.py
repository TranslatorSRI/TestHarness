"""Runs of a suite's old per-environment copies join the shared suite's history."""

from datetime import datetime, timedelta, timezone

from radiator import queries

from . import factories as f
from .test_ingest import _get, _harness_client

NOW = datetime.now(timezone.utc)


def _record(app, suite, env, days_ago):
    client = _harness_client(app)
    run = f.run_create(suite=suite, env=env, started_at=NOW - timedelta(days=days_ago))
    client.start_run(run)
    client.add_result(f.asset("Asset_1"))
    client.finish_run(counts={"PASSED": 1})
    assert not client.upload_failed
    return run.run_id


def test_an_old_name_is_filed_under_the_shared_suite(app):
    run_id = _record(app, "prod_integration", "prod", days_ago=7)
    run = _get(app, run_id)
    assert run.suite == "mvp_tests"
    assert run.source_suite == "prod_integration"


def test_a_current_name_is_kept(app):
    run = _get(app, _record(app, "mvp_tests", "prod", days_ago=0))
    assert (run.suite, run.source_suite) == ("mvp_tests", None)


def test_history_carries_on_in_each_environment(app):
    old_prod = _record(app, "prod_integration", "prod", days_ago=7)
    _record(app, "test_integration", "test", days_ago=6)
    new_prod = _record(app, "mvp_tests", "prod", days_ago=0)
    with app.state.sessionmaker() as session:
        run = queries.get_run(session, new_prod)
        # prod's previous run is prod's old copy, not test's
        assert queries.previous_run(session, run).id == old_prod
        history = queries.series_history(session, run)
        assert [r.id for r, _ in history] == [old_prod, new_prod]
        # and the Trends page's line
        trend = queries.agent_trends(session, "mvp_tests", "prod")
        assert [point.run.id for point in trend] == [old_prod, new_prod]


def test_the_run_page_says_what_it_was_recorded_as(app, browser):
    run_id = _record(app, "pathfinder_tests_TEST", "test", days_ago=1)
    page = browser.get(f"/runs/{run_id}")
    assert page.status_code == 200
    assert "Recorded as pathfinder_tests_TEST" in page.text
