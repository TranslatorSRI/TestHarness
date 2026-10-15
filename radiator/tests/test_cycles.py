"""Run cycles: which test a run is, flags, verdicts, the board and the digest."""

import uuid
from datetime import datetime, timedelta, timezone

import radiator_schema as schema
from radiator import cycles
from radiator.ingest import ingest_payload

from . import factories as f

T0 = datetime(2026, 9, 7, 6, tzinfo=timezone.utc)  # a Monday


def _assets(statuses, kind="acceptance", **agent_kw):
    """One asset per status, each answered by the ARS and aragorn alike."""
    out = []
    for i, status in enumerate(statuses):
        kw = {k: v[i] if isinstance(v, list) else v for k, v in agent_kw.items()}
        out.append(
            f.asset(
                f"A{i}",
                status=status,
                kind=kind,
                agents=[f.agent("ars", status), f.agent("aragorn", status, **kw)],
            )
        )
    return out


def _results_run(started, statuses, env="ci", cycle_id=None, kind="acceptance", **kw):
    suite = "pathfinder_tests" if kind == "pathfinder" else "sprint_4_tests"
    return schema.RunPayload(
        run=f.run_create(started_at=started, env=env, suite=suite, cycle_id=cycle_id),
        results=_assets(statuses, kind=kind, **kw),
        finish=f.finish(started),
    )


def _perf_run(
    started, msc, env="ci", cycle_id=None, target="ars", passed=True, error=None
):
    return schema.RunPayload(
        run=f.run_create(
            started_at=started,
            env=env,
            suite="performance_tests",
            target=target,
            target_url=f"https://{target}.{env}.transltr.io",
            cycle_id=cycle_id,
        ),
        performance=[
            schema.PerformanceResult(
                test_case_id="Perf_1",
                asset_id="Perf_mixed",
                host=f"https://{target}.{env}.transltr.io",
                helmsdeep_target=target,
                profile="mixed",
                status="PASSED" if passed else "FAILED",
                error=error,
                max_sustainable_concurrency=msc,
                checkpoints_passed=passed,
            )
        ],
        finish=f.finish(started, minutes=68),
    )


def _load(app, *payloads):
    with app.state.sessionmaker() as session:
        for payload in payloads:
            ingest_payload(session, payload)
        session.commit()


def _cycle(app, start, env="ci", acceptance=None, pathfinder=None, msc=None, **kw):
    cycle_id = uuid.uuid4()
    payloads = []
    if acceptance is not None:
        payloads.append(_results_run(start, acceptance, env, cycle_id, **kw))
    if pathfinder is not None:
        payloads.append(
            _results_run(
                start + timedelta(hours=1), pathfinder, env, cycle_id, "pathfinder"
            )
        )
    if msc is not None:
        payloads.append(
            _perf_run(start + timedelta(hours=1, minutes=20), msc, env, cycle_id)
        )
    _load(app, *payloads)
    return cycle_id


def _get_cycle(app, cycle_id):
    with app.state.sessionmaker() as session:
        return cycles.get_cycle(session, cycle_id)


P, F = "PASSED", "FAILED"


def test_a_cycle_board_compares_each_test_with_its_previous_run(app):
    _cycle(app, T0, acceptance=[P, P, F, F], pathfinder=[P, F], msc=10.0)
    later = _cycle(
        app,
        T0 + timedelta(days=7),
        acceptance=[P, F, P, P],  # A1 regressed; A2, A3 fixed
        pathfinder=[P, F],
        msc=7.0,
    )
    cycle = _get_cycle(app, later)
    assert cycle.env == "ci"
    assert [row.kind for row in cycle.rows] == [
        "acceptance",
        "pathfinder",
        "performance",
    ]
    acceptance, pathfinder, performance = cycle.rows

    assert acceptance.value == 0.75 and acceptance.previous == 0.5
    assert acceptance.change_text == "▲25.0 pts"
    # counted per agent result: the ARS and aragorn both flipped
    assert (acceptance.regressions, acceptance.fixed) == (2, 4)
    assert acceptance.verdict == cycles.IMPROVED
    assert [c.agent for c in acceptance.changes] == ["ars", "aragorn"]
    assert [a.asset_id for a in acceptance.changes[0].regressions] == ["A1"]
    assert acceptance.history == [0.5, 0.75]

    assert pathfinder.verdict == cycles.STEADY
    assert pathfinder.change_text == "no change"

    assert performance.label == "Performance · ars"
    assert performance.value_text == "7.0"
    assert performance.change_text == "▼3.0 (-30%)"
    assert performance.verdict == cycles.REGRESSED
    assert [flag.kind for flag in performance.flags] == ["concurrency_drop"]


def test_verdicts(app):
    first = _cycle(app, T0, acceptance=[P, F])
    assert _get_cycle(app, first).rows[0].verdict == cycles.FIRST
    for week, (statuses, verdict) in enumerate(
        [
            ([F, P], cycles.MIXED),  # one regression and one fix
            ([F, F], cycles.REGRESSED),
            ([P, P], cycles.IMPROVED),
            ([P, P], cycles.STEADY),
        ],
        start=1,
    ):
        cycle_id = _cycle(app, T0 + timedelta(days=7 * week), acceptance=statuses)
        assert _get_cycle(app, cycle_id).rows[0].verdict == verdict, verdict


def test_a_cycle_where_everything_was_skipped_has_no_result(app):
    _cycle(app, T0, acceptance=[P, P])
    cycle_id = _cycle(app, T0 + timedelta(days=7), acceptance=["SKIPPED", "SKIPPED"])
    row = _get_cycle(app, cycle_id).rows[0]
    assert row.verdict == cycles.NO_DATA
    assert row.value_text == "all skipped"


def test_performance_verdicts_and_flags(app):
    _cycle(app, T0, msc=10.0)
    for days, msc, verdict, flags in [
        (7, 10.5, cycles.STEADY, []),
        (14, 12.0, cycles.IMPROVED, []),
        (21, 8.0, cycles.REGRESSED, ["concurrency_drop"]),  # -33%
    ]:
        cycle_id = _cycle(app, T0 + timedelta(days=days), msc=msc)
        row = _get_cycle(app, cycle_id).rows[0]
        assert row.verdict == verdict, days
        assert [f.kind for f in row.flags] == flags, days

    cycle_id = uuid.uuid4()
    _load(
        app,
        _perf_run(
            T0 + timedelta(days=28), None, cycle_id=cycle_id, passed=False, error="boom"
        ),
    )
    row = _get_cycle(app, cycle_id).rows[0]
    assert row.verdict == cycles.NO_DATA
    assert row.value_text == "no result"
    assert [f.kind for f in row.flags] == ["perf_failed", "checkpoints"]
    assert "run failed: boom (was 8.0)" in row.flags[0].text


def _flags(app, before, after, at=T0):
    """The flags between two runs. ``before`` and ``after`` give aragorn's
    status and fields per asset; the ARS passes throughout."""

    def payload(started, spec):
        fields = {k: v for k, v in spec.items() if k != "status"}
        run = _results_run(started, [P] * len(spec["status"]), **fields)
        for asset, status in zip(run.results, spec["status"]):
            asset.agents[1] = asset.agents[1].model_copy(
                update={"status": schema.Status(status)}
            )
        return run

    a, b = payload(at, before), payload(at + timedelta(days=7), after)
    _load(app, a, b)
    with app.state.sessionmaker() as session:
        row = cycles.rows_for(session, [session.get(cycles.Run, b.run.run_id)])[0]
    return {flag.kind: flag for flag in row.flags}


def test_flags_no_results_on_assets_that_had_them(app):
    flags = _flags(
        app,
        {"status": [P] * 4, "n_results": [100] * 4},
        {"status": ["NO_RESULTS"] * 3 + [P], "n_results": [0, 0, 0, 100]},
    )
    assert set(flags) == {"no_results"}
    assert flags["no_results"].who == "aragorn"
    assert "no results on 3 assets" in flags["no_results"].text


def test_flags_need_enough_assets(app):
    flags = _flags(
        app,
        {"status": [P] * 4, "n_results": [100] * 4},
        {"status": ["NO_RESULTS"] * 2 + [P, P], "n_results": [0, 0, 100, 100]},
    )
    assert flags == {}


def test_flags_new_errors(app):
    flags = _flags(
        app,
        {"status": [P] * 3, "http_status": [200] * 3},
        {"status": ["ERROR"] * 3, "http_status": [502] * 3},
    )
    assert set(flags) == {"errors"}


def test_flags_slower_responses(app):
    flags = _flags(
        app,
        {"status": [P] * 3, "response_time_s": [4.0, 5.0, 6.0]},
        {"status": [P] * 3, "response_time_s": [14.0, 15.0, 16.0]},
    )
    assert flags["slower"].text == "aragorn: median response time 5.0s → 15.0s"
    # doubling a fast response isn't news
    assert (
        _flags(
            app,
            {"status": [P] * 3, "response_time_s": [1.0] * 3},
            {"status": [P] * 3, "response_time_s": [3.0] * 3},
            at=T0 + timedelta(days=14),
        )
        == {}
    )


def test_flags_rank_drops_while_passing(app):
    flags = _flags(
        app,
        {"status": [P, P], "rank": [2, 2]},
        {"status": [P, P], "rank": [40, 20]},
    )
    assert set(flags) == {"rank_drop"}
    assert "on 1 asset, still passing: A0 name (2 → 40)" in flags["rank_drop"].text


def test_digest_takes_each_environments_latest_full_runs(app):
    week_start = T0 + timedelta(days=7)
    _cycle(app, T0, "ci", acceptance=[P, F], msc=10.0)
    _cycle(app, week_start, "dev", acceptance=[F, F], pathfinder=[P])
    ci = _cycle(app, week_start + timedelta(days=1), "ci", acceptance=[P, P], msc=11.0)
    _load(
        app,
        # a slice and an override run aren't the environment's status
        _results_run(week_start + timedelta(days=2), [F, F], "ci"),
        _perf_run(week_start + timedelta(days=2), 6.0, "ci", target="arax"),
    )
    with app.state.sessionmaker() as session:
        # mark the later ci acceptance run as a query-type slice
        run = session.scalars(
            cycles.select(cycles.Run).where(
                cycles.Run.started_at == week_start + timedelta(days=2),
                cycles.Run.suite == "sprint_4_tests",
            )
        ).one()
        run.query_type = "MVP1"
        session.commit()
        d = cycles.digest(session, until=week_start + timedelta(days=6), days=7)
    assert [e.env for e in d.envs] == ["dev", "ci"]
    dev, ci_env = d.envs
    assert [r.kind for r in dev.rows] == ["acceptance", "pathfinder"]
    assert ci_env.cycle_ids == [ci]
    assert [r.label for r in ci_env.rows] == [
        "Acceptance",
        "Performance · ars",
        "Performance · arax",
    ]
    assert ci_env.cell("acceptance")[0].value == 1.0
    assert ci_env.cell("acceptance")[0].verdict == cycles.IMPROVED


def test_cycle_api_and_pngs(app, api):
    cycle_id = _cycle(app, T0, acceptance=[P, F], pathfinder=[P], msc=10.0)
    res = api.get(f"/api/cycles/{cycle_id}")
    assert res.status_code == 200
    body = res.json()
    assert body["env"] == "ci"
    assert [r["label"] for r in body["rows"]] == [
        "Acceptance",
        "Pathfinder",
        "Performance · ars",
    ]
    assert body["rows"][0]["verdict_label"] == "First run"
    assert api.get("/api/cycles").json()[0]["cycle_id"] == str(cycle_id)
    png = api.get(f"/api/cycles/{cycle_id}/board.png")
    assert png.headers["content-type"] == "image/png" and png.content[:4] == b"\x89PNG"
    assert api.get(f"/api/cycles/{uuid.uuid4()}").status_code == 404
    until = (T0 + timedelta(days=1)).isoformat()
    digest = api.get("/api/digest", params={"until": until}).json()
    assert [e["env"] for e in digest["envs"]] == ["ci"]
    png = api.get("/api/digest.png", params={"until": until})
    assert png.content[:4] == b"\x89PNG"


def test_cycle_and_weekly_pages(app, browser, client):
    cycle_id = _cycle(app, T0, acceptance=[P, F], pathfinder=[P], msc=10.0)
    page = browser.get(f"/cycles/{cycle_id}")
    assert page.status_code == 200
    assert "CI run cycle" in page.text and "First run" in page.text
    weekly = browser.get(
        "/weekly", params={"week_of": (T0 + timedelta(days=2)).date().isoformat()}
    )
    assert weekly.status_code == 200
    assert f"/cycles/{cycle_id}" in weekly.text
    assert browser.get(f"/cycles/{uuid.uuid4()}").status_code == 404
    assert client.get("/weekly", follow_redirects=False).status_code in (302, 303, 401)


def test_runs_keep_their_cycle_id(app, api):
    cycle_id = _cycle(app, T0, acceptance=[P])
    with app.state.sessionmaker() as session:
        run = session.scalars(cycles.select(cycles.Run)).one()
        assert run.cycle_id == cycle_id
