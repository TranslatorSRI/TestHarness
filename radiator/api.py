"""The JSON API: ingest for the harness, and a small read API for scripts."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

import radiator_schema as schema
from radiator import grid as run_grid
from radiator import history_png, ingest, queries
from radiator.auth import require_token, require_token_or_login
from radiator.models import Run

ingest_router = APIRouter(
    prefix=schema.INGEST_PREFIX, dependencies=[Depends(require_token)]
)
read_router = APIRouter(prefix="/api", dependencies=[Depends(require_token_or_login)])


def get_session(request: Request):
    with request.app.state.sessionmaker() as session:
        yield session


def _commit_or_404(session: Session, run_id: uuid.UUID, write):
    try:
        write()
    except ingest.RunNotFound:
        session.rollback()
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No run {run_id}: open it with POST {schema.INGEST_PREFIX}/runs first",
        )
    session.commit()


@ingest_router.post("/runs", status_code=status.HTTP_201_CREATED)
def create_run(run: schema.RunCreate, session: Session = Depends(get_session)):
    ingest.upsert_run(session, run)
    session.commit()
    return {"run_id": str(run.run_id)}


@ingest_router.post("/runs/{run_id}/results")
def add_results(
    run_id: uuid.UUID,
    batch: schema.ResultBatch,
    session: Session = Depends(get_session),
):
    _commit_or_404(
        session, run_id, lambda: ingest.upsert_results(session, run_id, batch)
    )
    return {"results": len(batch.results), "performance": len(batch.performance)}


@ingest_router.patch("/runs/{run_id}")
def finish_run(
    run_id: uuid.UUID,
    finish: schema.RunFinish,
    session: Session = Depends(get_session),
):
    _commit_or_404(session, run_id, lambda: ingest.finish_run(session, run_id, finish))
    return {"run_id": str(run_id)}


@read_router.get("/runs")
def list_runs(
    suite: str | None = None,
    env: str | None = None,
    limit: int = 50,
    session: Session = Depends(get_session),
):
    runs = queries.list_runs(session, suite=suite, env=env, limit=min(limit, 500))
    return [queries.run_json(run) for run in runs]


@read_router.get("/runs/{run_id}")
def get_run(
    run_id: uuid.UUID, results: bool = True, session: Session = Depends(get_session)
):
    """A run, with every result unless ``results=false``."""
    if results:
        run = queries.get_run(session, run_id)
    else:
        run = session.get(Run, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such run")
    return queries.run_json(run, with_results=results)


def _run_or_404(session: Session, run_id: uuid.UUID) -> Run:
    run = session.get(Run, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such run")
    return run


@read_router.get("/runs/{run_id}/summary")
def get_run_summary(run_id: uuid.UUID, session: Session = Depends(get_session)):
    """The run against the previous one in its series: pass rates,
    regressions, fixes. For notifications such as the Slack report."""
    return queries.run_summary(session, _run_or_404(session, run_id))


@read_router.get("/runs/{run_id}/history.png")
def get_run_history_png(
    run_id: uuid.UUID, runs: int = 30, session: Session = Depends(get_session)
):
    """Pass rate by agent over the series' last ``runs`` runs, ending at this
    one, as a PNG (for Slack, which can't show the dashboard's charts)."""
    run = _run_or_404(session, run_id)
    history = queries.series_history(session, run, limit=min(max(runs, 1), 120))
    return Response(
        history_png.render(run, history),
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=300"},
    )


@read_router.get("/runs/{run_id}/performance.png")
def get_run_performance_png(
    run_id: uuid.UUID, runs: int = 30, session: Session = Depends(get_session)
):
    """Each of the run's services' max sustainable concurrency over its last
    ``runs`` runs, ending at this one, as a PNG (for Slack)."""
    run = queries.get_run(session, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such run")
    if not run.performance:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No performance results")
    series = queries.performance_series(session, run, limit=min(max(runs, 1), 120))
    return Response(
        history_png.render_performance(run, series),
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=300"},
    )


GRID_TITLE = "Acceptance run grid"
GRID_MAX_PER_LANE = 10


def grid_subtitle(suite: str, days: float) -> str:
    return (
        f"{suite} · environment × time, last {days:.0f} days · cards show pass "
        "rate, passed / failed / skipped"
    )


@read_router.get("/runs/{run_id}/grid.png")
def get_run_grid_png(
    request: Request,
    run_id: uuid.UUID,
    days: int = 35,
    session: Session = Depends(get_session),
):
    """The acceptance run grid for this run's suite, up to this run, with the
    run marked NEW, as a PNG (for Slack)."""
    run = _run_or_404(session, run_id)
    days = min(max(days, 1), 180)
    grid = run_grid.build(
        session,
        run.suite,
        until=run.started_at,
        days=days,
        width=1600,
        excluded_envs=request.app.state.settings.excluded_envs,
        new_run_id=run.id,
        # a busy environment narrows the window, so it stays readable
        max_per_lane=GRID_MAX_PER_LANE,
    )
    shown_days = (grid.until - grid.since).total_seconds() / 86400
    return Response(
        run_grid.to_png(grid, GRID_TITLE, grid_subtitle(run.suite, shown_days)),
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=300"},
    )
