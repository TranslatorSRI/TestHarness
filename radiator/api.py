"""The JSON API: ingest for the harness, and a small read API for scripts."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

import radiator_schema as schema
from radiator import ingest, queries
from radiator.auth import require_token, require_token_or_login

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
def get_run(run_id: uuid.UUID, session: Session = Depends(get_session)):
    run = queries.get_run(session, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such run")
    return queries.run_json(run, with_results=True)
