"""The JSON API: ingest for the harness, and an open read API for scripts.

Uploads need the bearer token. Reading needs nothing: anyone can pull the
results for their own analysis (see docs/api.md).
"""

import csv
import io
import logging
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

import radiator_schema as schema
from radiator import board_png, cycles, history_png, ingest, queries
from radiator.auth import require_token
from radiator.models import Run

logger = logging.getLogger("radiator")

ingest_router = APIRouter(
    prefix=schema.INGEST_PREFIX, dependencies=[Depends(require_token)], tags=["ingest"]
)
read_router = APIRouter(prefix="/api", tags=["read"])


def get_session(request: Request):
    with request.app.state.sessionmaker() as session:
        yield session


def _commit_or_404(session: Session, run_id: uuid.UUID, write):
    try:
        write()
    except ingest.RunNotFound:
        session.rollback()
        logger.warning(f"Upload for unknown run {run_id}: it was never opened")
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No run {run_id}: open it with POST {schema.INGEST_PREFIX}/runs first",
        )
    session.commit()


@ingest_router.post("/runs", status_code=status.HTTP_201_CREATED)
def create_run(run: schema.RunCreate, session: Session = Depends(get_session)):
    ingest.upsert_run(session, run)
    session.commit()
    logger.info(
        f"Run {run.run_id} opened: {run.suite} in {run.env or 'no environment'}"
        + (f", target {run.target}" if run.target else "")
        + (
            f", {run.expected_assets} assets expected"
            if run.expected_assets is not None
            else ""
        )
    )
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
    logger.info(
        f"Run {run_id}: {len(batch.results)} asset results, "
        f"{len(batch.performance)} performance results"
    )
    return {"results": len(batch.results), "performance": len(batch.performance)}


@ingest_router.patch("/runs/{run_id}")
def finish_run(
    run_id: uuid.UUID,
    finish: schema.RunFinish,
    session: Session = Depends(get_session),
):
    _commit_or_404(session, run_id, lambda: ingest.finish_run(session, run_id, finish))
    logger.info(f"Run {run_id} finished")
    return {"run_id": str(run_id)}


@read_router.get("/runs")
def list_runs(
    suite: str | None = None,
    env: str | None = None,
    origin: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    zebrunner_launch: str | None = None,
    limit: int = 50,
    offset: int = 0,
    session: Session = Depends(get_session),
):
    """Runs, newest first. ``since``/``until`` bound when they started;
    ``zebrunner_launch`` finds a run imported from Zebrunner by its old
    ``testRunId``. Page with ``limit`` (at most 500) and ``offset``."""
    runs = queries.list_runs(
        session,
        suite=suite,
        env=env,
        origin=origin,
        since=since,
        until=until,
        zebrunner_launch=zebrunner_launch,
        limit=min(max(limit, 1), 500),
        offset=max(offset, 0),
    )
    return [queries.run_json(run) for run in runs]


@read_router.get("/runs/latest")
def get_latest_run(
    suite: str,
    env: str | None = None,
    finished: bool = True,
    results: bool = False,
    session: Session = Depends(get_session),
):
    """The newest run of a suite (in ``env``, if given): only finished runs
    unless ``finished=false``. With every result if ``results=true``."""
    run = queries.latest_run(session, suite=suite, env=env, finished=finished)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such run")
    if results:
        run = queries.get_run(session, run.id)
    return queries.run_json(run, with_results=results)


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


def _rows_response(rows: list[dict], columns: list[str], format: str, name: str):
    if format == "json":
        return rows
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {k: ";".join(v) if isinstance(v, list) else v for k, v in row.items()}
        )
    return Response(
        out.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}.csv"'},
    )


def _loaded_run_or_404(session: Session, run_id: uuid.UUID) -> Run:
    run = queries.get_run(session, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such run")
    return run


@read_router.get("/runs/{run_id}/results")
def get_run_results(
    run_id: uuid.UUID,
    agent: str | None = None,
    status_: str | None = Query(None, alias="status"),
    test_case_id: str | None = None,
    format: Literal["json", "csv"] = "json",
    session: Session = Depends(get_session),
):
    """A run's results flattened to one row per asset and agent, for
    loading into a dataframe. Filter by ``agent``, the agent's ``status``
    and ``test_case_id``; ``format=csv`` for a CSV file."""
    rows = [
        row
        for row in queries.result_rows(_loaded_run_or_404(session, run_id))
        if (agent is None or row["agent"] == agent)
        and (status_ is None or row["status"] == status_)
        and (test_case_id is None or row["test_case_id"] == test_case_id)
    ]
    return _rows_response(rows, queries.RESULT_ROW_COLUMNS, format, f"results_{run_id}")


@read_router.get("/runs/{run_id}/pks")
def get_run_pks(
    run_id: uuid.UUID,
    agent: str | None = None,
    format: Literal["json", "csv"] = "json",
    session: Session = Depends(get_session),
):
    """Every pk in a run: one row per asset and agent, with the asset's
    parent pk. ``format=csv`` for a CSV file."""
    rows = [
        row
        for row in queries.pk_rows(_loaded_run_or_404(session, run_id))
        if agent is None or row["agent"] == agent
    ]
    return _rows_response(rows, queries.PK_ROW_COLUMNS, format, f"pks_{run_id}")


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


@read_router.get("/runs/{run_id}/progress")
def get_run_progress(run_id: uuid.UUID, session: Session = Depends(get_session)):
    """How far a run has got: what the run page polls while it's running."""
    return queries.run_progress(session, _run_or_404(session, run_id))


@read_router.get("/live")
def get_live(session: Session = Depends(get_session)):
    """The runs in progress: what the runs list and the weekly page poll."""
    return queries.live_status(session)


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


def _png(data: bytes) -> Response:
    return Response(
        data, media_type="image/png", headers={"Cache-Control": "private, max-age=300"}
    )


@read_router.get("/cycles")
def list_cycles(limit: int = 20, session: Session = Depends(get_session)):
    """The latest run cycles, newest first."""
    return [
        {"cycle_id": str(cycle_id), "env": env, "started_at": started.isoformat()}
        for cycle_id, env, started in cycles.recent_cycles(
            session, limit=min(max(limit, 1), 200)
        )
    ]


def _cycle_or_404(session: Session, cycle_id: uuid.UUID) -> cycles.Cycle:
    cycle = cycles.get_cycle(session, cycle_id)
    if cycle is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such cycle")
    return cycle


@read_router.get("/cycles/{cycle_id}")
def get_cycle(cycle_id: uuid.UUID, session: Session = Depends(get_session)):
    """A run cycle's board: each test against its previous run, with the
    regressions and fixes by agent, and flags. For the Slack report."""
    return cycles.cycle_json(_cycle_or_404(session, cycle_id))


@read_router.get("/cycles/{cycle_id}/board.png")
def get_cycle_board_png(cycle_id: uuid.UUID, session: Session = Depends(get_session)):
    """The cycle's board as a PNG (for Slack)."""
    return _png(board_png.board_png(_cycle_or_404(session, cycle_id)))


def _digest(session: Session, days: int, until: datetime | None) -> cycles.Digest:
    return cycles.digest(session, until=until, days=min(max(days, 1), 31))


@read_router.get("/digest")
def get_digest(
    days: int = 7,
    until: datetime | None = None,
    session: Session = Depends(get_session),
):
    """Every environment's latest runs of each test over the last ``days``."""
    return cycles.digest_json(_digest(session, days, until))


@read_router.get("/digest.png")
def get_digest_png(
    days: int = 7,
    until: datetime | None = None,
    session: Session = Depends(get_session),
):
    """The weekly digest as a PNG (for Slack)."""
    return _png(board_png.digest_png(_digest(session, days, until)))
