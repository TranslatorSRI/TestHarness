"""The Zebrunner import, against a fake Zebrunner speaking its read API."""

import json

import httpx
from fastapi.testclient import TestClient

from radiator import queries
from radiator.zebrunner_import import (
    Radiator,
    Zebrunner,
    convert_test,
    infer_env,
    import_runs,
    run_id_for,
    suite_from_name,
)

from .conftest import TOKEN

ZE = "http://zebrunner.test"


def label(key, value):
    return {"key": key, "value": value}


def acceptance_test(test_id, asset, agents, status="PASSED", expected="TopAnswer"):
    return {
        "id": test_id,
        "name": f"{asset} name",
        "status": status,
        "testClass": "TestCase_1",
        "labels": [
            label("TestCase", "TestCase_1"),
            label("TestAsset", asset),
            label("ExpectedOutput", expected),
            label("InputCurie", "MONDO:0004979"),
            label("OutputCurie", "CHEBI:31690"),
            *[label(agent, s) for agent, s in agents.items()],
        ],
    }


REPORT = {
    "pks": {"parent_pk": "parent-1", "ars": "merged-1", "aragorn": "child-1"},
    "result": {
        "ars": {
            "status": "PASSED",
            "message": None,
            "actual_output": {"found": True, "ars_rank": 3, "ars_score": 0.97},
        },
        "aragorn": {
            "status": "FAILED",
            "message": None,
            "actual_output": {"found": True, "ara_rank": 88, "ara_score": 0.2},
        },
    },
    "test_details": None,
}

LAUNCHES = [
    {
        "id": 101,
        "name": "sprint_4_tests: 2025_03_02_06_00",
        "startedAt": "2025-03-02T06:00:00Z",
        "endedAt": "2025-03-02T06:50:00Z",
        "build": "v0.3.3",
    },
    {
        "id": 100,
        "name": "sprint_4_tests: 2025_03_01_06_00",
        "startedAt": "2025-03-01T06:00:00Z",
        "endedAt": "2025-03-01T06:45:00Z",
        "build": "v0.3.3",
    },
    {
        "id": 99,
        "name": "performance_tests: 2025_02_28_06_00",
        "startedAt": "2025-02-28T06:00:00Z",
        "endedAt": "2025-02-28T07:00:00Z",
    },
]
TESTS = {
    101: [
        acceptance_test(1, "Asset_1", {"ars": "PASSED", "aragorn": "FAILED"}),
        # Zebrunner refused the NO_RESULTS finish, so the test is IN_PROGRESS;
        # the agent label still has the harness's status
        acceptance_test(
            2,
            "Asset_2",
            {"ars": "NO_RESULTS", "aragorn": "SKIPPED"},
            status="IN_PROGRESS",
        ),
    ],
    100: [
        acceptance_test(
            3, "Asset_1", {"ars": "FAILED", "aragorn": "FAILED"}, status="FAILED"
        )
    ],
    99: [
        {
            "id": 4,
            "name": "perf",
            "status": "PASSED",
            "labels": [
                label("TestCase", "Perf_1"),
                label("TestAsset", "Asset_9"),
                label("TestRunTime", "60"),
                label("UserSpawnRate", "1"),
            ],
        }
    ],
}
LOGS = {
    1: [
        {"kind": "LOG", "message": json.dumps({"message": {"query_graph": {}}})},
        {"kind": "LOG", "message": json.dumps(REPORT, indent=4)},
    ],
}


def fake_zebrunner(calls):
    """Zebrunner's read API, with an access token that expires once."""
    state = {"expired_once": False}

    def handler(request: httpx.Request):
        calls.append((request.method, request.url.path, dict(request.url.params)))
        path = request.url.path
        if path == "/api/iam/v1/auth/refresh":
            assert json.loads(request.content) == {"refreshToken": "refresh"}
            return httpx.Response(200, json={"authToken": "access"})
        assert request.headers["Authorization"] == "Bearer access"
        if path == "/api/reporting/v1/launches":
            if not state["expired_once"]:
                state["expired_once"] = True
                return httpx.Response(401)
            page, size = int(request.url.params["page"]), int(
                request.url.params["pageSize"]
            )
            items = LAUNCHES[(page - 1) * size : page * size]
            return httpx.Response(200, json={"items": items, "_meta": {}})
        if path.startswith("/api/reporting/v1/launches/") and path.endswith("/tests"):
            launch_id = int(path.split("/")[5])
            return httpx.Response(200, json={"items": TESTS[launch_id]})
        if path.endswith("/logs"):
            test_id = int(path.split("/")[7])
            logs = LOGS.get(test_id, [])
            # one log per page, to exercise the page token
            token = int(request.url.params.get("pageToken", 0))
            meta = {"nextPageToken": str(token + 1)} if token + 1 < len(logs) else {}
            return httpx.Response(
                200, json={"items": logs[token : token + 1], "_meta": meta}
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _radiator(app):
    radiator = Radiator.__new__(Radiator)
    radiator.client = TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"})
    return radiator


def _zebrunner(calls):
    zebrunner = Zebrunner(ZE, "refresh", project_id=7, transport=fake_zebrunner(calls))
    return zebrunner


def test_import(app):
    calls = []
    stats = import_runs(_zebrunner(calls), _radiator(app), env="ci")

    assert stats.imported == 2
    assert stats.skipped_empty == 1  # the performance-only run
    assert stats.performance_tests_skipped == 1
    assert not stats.failed
    # every Zebrunner call carried the project id, paging worked through the 401
    assert all(
        params.get("projectId") == "7"
        for _, path, params in calls
        if "auth" not in path
    )

    with app.state.sessionmaker() as session:
        latest = queries.get_run(session, run_id_for(ZE, 101))
        assert latest.origin == "zebrunner_import"
        assert latest.origin_ref == "101"
        assert latest.suite == "sprint_4_tests"
        assert latest.env == "ci"
        assert latest.harness_version == "v0.3.3"
        assert latest.counts == {"PASSED": 1, "NO_RESULTS": 1}

        assets = {a.asset_id: a for a in latest.assets}
        one = assets["Asset_1"]
        assert one.status == "PASSED"
        assert one.parent_pk == "parent-1"
        agents = {a.agent: a for a in one.agents}
        assert (
            agents["ars"].found,
            agents["ars"].rank,
            agents["ars"].score,
            agents["ars"].pk,
        ) == (True, 3, 0.97, "merged-1")
        assert (agents["aragorn"].status, agents["aragorn"].rank) == ("FAILED", 88)
        # what Zebrunner never had stays unknown, not zero
        assert agents["ars"].n_results is None and agents["ars"].http_status is None

        two = assets["Asset_2"]
        assert two.status == "NO_RESULTS"
        assert {a.agent: a.status for a in two.agents} == {
            "ars": "NO_RESULTS",
            "aragorn": "SKIPPED",
        }

        # the two imported runs are one series, so they diff against each other
        previous = queries.previous_run(session, latest)
        assert previous.id == run_id_for(ZE, 100)


def test_reimport_skips_then_force_updates(app):
    import_runs(_zebrunner([]), _radiator(app), env="ci")
    again = import_runs(_zebrunner([]), _radiator(app), env="ci")
    assert again.imported == 0 and again.skipped_existing == 2

    forced = import_runs(_zebrunner([]), _radiator(app), env="ci", force=True)
    assert forced.imported == 2
    with app.state.sessionmaker() as session:
        assert len(queries.list_runs(session)) == 2


def test_dry_run_writes_nothing(app):
    stats = import_runs(_zebrunner([]), None, since=None, fetch_logs=False)
    assert stats.imported == 2
    # no env given: the prod-only aragorn agent marks these as prod runs
    assert stats.unknown_env == 0
    with app.state.sessionmaker() as session:
        assert queries.list_runs(session) == []


def test_filters(app):
    from datetime import datetime, timezone

    stats = import_runs(
        _zebrunner([]),
        _radiator(app),
        env="ci",
        since=datetime(2025, 3, 2, tzinfo=timezone.utc),
    )
    assert stats.launches == 1 and stats.imported == 1


def test_conversion_details():
    assert suite_from_name("sprint_4_tests: 2025_03_02_06_00") == "sprint_4_tests"
    assert suite_from_name("odd name") == "odd name"
    # an asset from a target-override run: the single agent drives the status
    result = convert_test(acceptance_test(5, "Asset_5", {"aragorn": "FAILED"}), None)
    assert result.status == "FAILED"
    # malformed report values are dropped, not fatal
    report = {
        "result": {"ars": {"status": "PASSED", "actual_output": {"ars_rank": "n/a"}}}
    }
    result = convert_test(acceptance_test(6, "Asset_6", {"ars": "PASSED"}), report)
    assert result.agents[0].rank is None


def test_env_inference():
    prod = convert_test(
        acceptance_test(7, "A", {"ars": "PASSED", "arax": "PASSED"}), None
    )
    shepherd = convert_test(
        acceptance_test(8, "A", {"ars": "PASSED", "shepherd-arax": "PASSED"}), None
    )
    assert infer_env([prod]) == "prod"
    # dev, ci and test all use the shepherd agents: can't tell which
    assert infer_env([shepherd]) is None
