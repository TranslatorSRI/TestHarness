"""Builders for schema payloads in tests and the demo seed."""

import uuid
from datetime import datetime, timedelta, timezone

import radiator_schema as schema


def run_create(started_at=None, **kwargs):
    defaults = dict(
        run_id=uuid.uuid4(),
        suite="sprint_4_tests",
        env="ci",
        started_at=started_at or datetime.now(timezone.utc),
    )
    defaults.update(kwargs)
    return schema.RunCreate(**defaults)


def agent(name, status="PASSED", **kwargs):
    return schema.AgentResult(agent=name, status=status, **kwargs)


def asset(
    asset_id="Asset_1",
    test_case_id="TestCase_1",
    status="PASSED",
    agents=None,
    **kwargs,
):
    defaults = dict(
        kind="acceptance",
        name=f"{asset_id} name",
        expected_output="TopAnswer",
        predicate="biolink:treats",
        input_curie="MONDO:0004979",
        output_curie="CHEBI:31690",
    )
    defaults.update(kwargs)
    return schema.AssetResult(
        test_case_id=test_case_id,
        asset_id=asset_id,
        status=status,
        agents=agents if agents is not None else [agent("ars", status)],
        **defaults,
    )


def finish(started_at=None, minutes=42, counts=None):
    started_at = started_at or datetime.now(timezone.utc)
    return schema.RunFinish(
        ended_at=started_at + timedelta(minutes=minutes), counts=counts or {}
    )
