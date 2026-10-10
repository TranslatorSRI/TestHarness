"""The acceptance run grid: trends, layout, and where it's shown."""

from datetime import datetime, timedelta, timezone

import radiator_schema as schema
from radiator import grid, queries
from radiator.ingest import ingest_payload

from . import factories as f

T0 = datetime(2026, 9, 1, 6, tzinfo=timezone.utc)


def _run(started, env="ci", passed=8, failed=2, skipped=0, finished=True, **kw):
    results = (
        [f.asset(f"P{i}") for i in range(passed)]
        + [
            f.asset(f"F{i}", status="FAILED", agents=[f.agent("ars", "FAILED")])
            for i in range(failed)
        ]
        + [
            f.asset(f"S{i}", status="SKIPPED", agents=[f.agent("ars", "SKIPPED")])
            for i in range(skipped)
        ]
    )
    return schema.RunPayload(
        run=f.run_create(started_at=started, env=env, **kw),
        results=results,
        finish=f.finish(started) if finished else None,
    )


def _load(app, *payloads):
    with app.state.sessionmaker() as session:
        for payload in payloads:
            ingest_payload(session, payload)
        session.commit()


def _items(app, **kw):
    with app.state.sessionmaker() as session:
        return grid.grid_runs(
            session,
            "sprint_4_tests",
            kw.pop("since", T0 - timedelta(days=1)),
            kw.pop("until", T0 + timedelta(days=60)),
            **kw,
        )


def test_trends_compare_pass_rates_within_an_environment(app):
    _load(
        app,
        _run(T0, passed=8, failed=2),  # 80%: BASE
        _run(T0 + timedelta(days=1), passed=9, failed=1),  # 90%: UP
        # a smaller run at the same rate is FLAT, not DOWN
        _run(T0 + timedelta(days=2), passed=18, failed=2),
        _run(T0 + timedelta(days=3), passed=7, failed=3),  # 70%: DOWN
        _run(T0 + timedelta(days=4), passed=0, failed=0, skipped=10),  # no data
        # compared with the last run that had a pass rate (70%)
        _run(T0 + timedelta(days=5), passed=7, failed=3),
        _run(T0 + timedelta(days=1), env="test", passed=5, failed=5),
        _run(T0 + timedelta(days=2), env="dev", passed=1, failed=9),
    )
    items = _items(app)
    ci = [
        (i.trend, round(i.rate, 2) if i.rate is not None else None)
        for i in items
        if i.run.env == "ci"
    ]
    assert ci == [
        ("base", 0.8),
        ("up", 0.9),
        ("flat", 0.9),
        ("down", 0.7),
        ("nodata", None),
        ("flat", 0.7),
    ]
    up = next(i for i in items if i.trend == "up")
    assert round(up.delta, 1) == 10.0
    assert [i.trend for i in items if i.run.env == "test"] == ["base"]
    assert [i.trend for i in items if i.run.env == "dev"] == ["excl"]


def test_only_finished_full_runs_are_shown(app):
    _load(
        app,
        _run(T0),
        _run(T0 + timedelta(hours=1), target="aragorn", target_url="http://x"),
        _run(T0 + timedelta(hours=2), query_type="MVP1"),
        _run(T0 + timedelta(hours=3), finished=False),
        _run(T0 + timedelta(hours=4), suite="other_suite"),
    )
    assert len(_items(app)) == 1


def test_a_run_before_the_window_is_the_baseline(app):
    _load(
        app,
        _run(T0, passed=5, failed=5),
        _run(T0 + timedelta(days=20), passed=6, failed=4),
    )
    [item] = _items(app, since=T0 + timedelta(days=10))
    assert item.trend == "up" and round(item.delta, 1) == 10.0


def _item(started, env="ci"):
    class R:
        pass

    run = R()
    run.started_at, run.env, run.number, run.id = started, env, 1, None
    return grid.GridRun(run, 1, 0, 0, 1.0)


def test_layout_stacks_runs_too_close_to_sit_side_by_side():
    since, until = T0, T0 + timedelta(days=10)
    items = [
        _item(T0 + timedelta(days=1)),
        _item(T0 + timedelta(days=1, hours=6)),  # overlaps the first: stacks
        _item(T0 + timedelta(days=8)),  # clear of both: back on top
        _item(T0 + timedelta(days=2), env="prod"),
    ]
    g = grid.layout("s", items, since, until, width=1180)
    assert [lane.env for lane in g.lanes] == ["ci", "prod"]
    assert [c.level for c in g.lanes[0].cards] == [0, 1, 0]
    # each run joined to the one before it in its lane
    assert len(g.links) == 2
    # lanes don't overlap, and the busier one is taller
    assert g.lanes[1].y == g.lanes[0].height > g.lanes[1].height


def test_window_narrows_to_fit_a_busy_environment():
    until = T0 + timedelta(days=30)
    daily = [_item(T0 + timedelta(days=d)) for d in range(30)]
    since = grid.fit_window(daily, T0, until, max_per_lane=10)
    assert sum(1 for i in daily if i.run.started_at >= since) == 10
    # but never narrower than a week
    hourly = [_item(until - timedelta(hours=h)) for h in range(100)]
    assert grid.fit_window(hourly, T0, until, 10) == until - timedelta(days=7)


def test_grid_png_and_page(app, api, browser):
    payloads = [_run(T0 + timedelta(days=d)) for d in range(3)]
    _load(app, *payloads)
    last = payloads[-1].run.run_id

    res = api.get(f"/api/runs/{last}/grid.png")
    assert res.status_code == 200 and res.content.startswith(b"\x89PNG")

    page = browser.get("/grid", params={"days": 90}).text
    assert "Acceptance run grid" in page
    assert f'href="/runs/{last}"' in page
    assert "BASE" in page and "FLAT" in page


def test_runs_are_numbered_in_arrival_order_and_keep_their_number(app, browser):
    first, second = _run(T0), _run(T0 + timedelta(days=1))
    _load(app, first, second)
    _load(app, first)  # re-uploaded: same number
    with app.state.sessionmaker() as session:
        a = queries.get_run(session, first.run.run_id)
        b = queries.get_run(session, second.run.run_id)
        assert b.number == a.number + 1
    assert f"#{b.number}" in browser.get(f"/runs/{second.run.run_id}").text
    assert f"#{a.number}</a>" in browser.get("/").text
