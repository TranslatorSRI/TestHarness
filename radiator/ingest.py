"""Writing results: what the ingest API and the Zebrunner importer share.

Every write is an upsert on the identity ``radiator_schema`` defines, so a
retried batch or a replayed run updates what's there instead of duplicating it.
An asset's agent rows are replaced wholesale on each upsert: the newest upload
is the whole truth about that asset in that run.
"""

import uuid

from sqlalchemy import delete, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

import radiator_schema as schema
from radiator.models import AgentResult, AssetResult, PerformanceResult, Run


class RunNotFound(LookupError):
    pass


def upsert_run(session: Session, run: schema.RunCreate) -> None:
    values = run.model_dump(exclude={"run_id"})
    stmt = pg_insert(Run).values(id=run.run_id, counts={}, **values)
    session.execute(stmt.on_conflict_do_update(index_elements=[Run.id], set_=values))


def _require_run(session: Session, run_id: uuid.UUID) -> None:
    if session.get(Run, run_id) is None:
        raise RunNotFound(run_id)


def upsert_results(
    session: Session, run_id: uuid.UUID, batch: schema.ResultBatch
) -> None:
    _require_run(session, run_id)
    for result in batch.results:
        values = result.model_dump(exclude={"agents"})
        values["status"] = result.status.value
        stmt = pg_insert(AssetResult).values(run_id=run_id, **values)
        asset_result_id = session.execute(
            stmt.on_conflict_do_update(
                index_elements=[
                    AssetResult.run_id,
                    AssetResult.test_case_id,
                    AssetResult.asset_id,
                ],
                set_=values,
            ).returning(AssetResult.id)
        ).scalar_one()
        session.execute(
            delete(AgentResult).where(AgentResult.asset_result_id == asset_result_id)
        )
        if result.agents:
            session.execute(
                insert(AgentResult),
                [
                    {
                        **agent.model_dump(),
                        "status": agent.status.value,
                        "asset_result_id": asset_result_id,
                    }
                    for agent in result.agents
                ],
            )

    for perf in batch.performance:
        values = perf.model_dump()
        values["status"] = perf.status.value
        stmt = pg_insert(PerformanceResult).values(run_id=run_id, **values)
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=[
                    PerformanceResult.run_id,
                    PerformanceResult.test_case_id,
                    PerformanceResult.asset_id,
                    PerformanceResult.host,
                ],
                set_=values,
            )
        )


def finish_run(session: Session, run_id: uuid.UUID, finish: schema.RunFinish) -> None:
    _require_run(session, run_id)
    session.execute(
        update(Run)
        .where(Run.id == run_id)
        .values(ended_at=finish.ended_at, counts=finish.counts)
    )


def ingest_payload(session: Session, payload: schema.RunPayload) -> None:
    """Write a whole run at once (the importer's path)."""
    upsert_run(session, payload.run)
    upsert_results(
        session,
        payload.run.run_id,
        schema.ResultBatch(results=payload.results, performance=payload.performance),
    )
    if payload.finish is not None:
        finish_run(session, payload.run.run_id, payload.finish)


def run_exists(session: Session, run_id: uuid.UUID) -> bool:
    return session.scalar(select(Run.id).where(Run.id == run_id)) is not None
