"""The Zebrunner import, against a fake Zebrunner speaking its read API."""

import json
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from radiator import queries
from radiator.zebrunner_import import (
    ArsEnvResolver,
    Radiator,
    Zebrunner,
    ZebrunnerError,
    ars_hosts_from_smartapi,
    convert_test,
    infer_env,
    import_runs,
    report_pks,
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
        if path.endswith("/logs-and-screenshots"):
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


def test_a_partly_imported_run_is_imported_again(app, api):
    """A run that exists but never got its end time stopped partway, and a
    re-run finishes it instead of skipping it."""
    run_id = run_id_for(ZE, 101)
    api.post(
        "/api/ingest/runs",
        json={
            "run_id": str(run_id),
            "suite": "sprint_4_tests",
            "started_at": "2025-03-02T06:00:00Z",
            "origin": "zebrunner_import",
        },
    )
    stats = import_runs(_zebrunner([]), _radiator(app), env="ci")
    assert stats.imported == 2 and stats.skipped_existing == 0
    with app.state.sessionmaker() as session:
        run = queries.get_run(session, run_id)
        assert run.ended_at is not None and len(run.assets) == 2


def test_zebrunner_errors_say_what_zebrunner_said():
    def handler(request: httpx.Request):
        if request.url.path == "/api/iam/v1/auth/refresh":
            return httpx.Response(200, json={"authToken": "access"})
        return httpx.Response(
            400, json={"message": "Required parameter 'projectId' is not present"}
        )

    zebrunner = Zebrunner(ZE, "refresh", transport=httpx.MockTransport(handler))
    with pytest.raises(ZebrunnerError) as error:
        next(zebrunner.launches())
    message = str(error.value)
    assert "/api/reporting/v1/launches" in message and "answered 400" in message
    assert "projectId" in message and "pass --project-id" in message


def _epoch_ms(text):
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)


def fake_legacy_zebrunner(calls, logs=True):
    """An older Zebrunner (eg 1.28): no /v1 read API; launches and tests come
    from the legacy search endpoints, in `results`, with the suite nested and
    times in epoch millis; and test logs aren't where the import looks."""

    def handler(request: httpx.Request):
        calls.append((request.method, request.url.path, dict(request.url.params)))
        path = request.url.path
        if path == "/api/iam/v1/auth/refresh":
            return httpx.Response(200, json={"authToken": "access"})
        if path == "/api/reporting/v1/launches":
            return httpx.Response(
                404,
                json={"status": 404, "error": "Not Found", "path": "/v1/launches"},
            )
        if path == "/api/reporting/api/tests/runs/search":
            page, size = int(request.url.params["page"]), int(
                request.url.params["pageSize"]
            )
            runs = [
                {
                    "id": launch["id"],
                    "testSuite": {"name": launch["name"]},
                    "startedAt": _epoch_ms(launch["startedAt"]),
                    "elapsed": 3000,
                }
                for launch in LAUNCHES
            ]
            return httpx.Response(
                200, json={"results": runs[(page - 1) * size : page * size]}
            )
        if path.endswith("/logs") and "/v1/test-runs/" in path and logs:
            test_id = int(path.split("/")[7])
            entries = [
                {"level": "INFO", "message": log["message"], "timestamp": 1791954010}
                for log in LOGS.get(test_id, [])
            ]
            # one entry per page, to exercise the paging (no ids to go by)
            page = int(request.url.params["page"])
            return httpx.Response(
                200,
                json={
                    "page": page,
                    "totalPages": max(len(entries), 1),
                    "results": entries[page - 1 : page],
                },
            )
        if path == "/api/reporting/api/tests/search" and request.method == "POST":
            body = json.loads(request.content)
            page, size = body["page"], body["pageSize"]
            tests = TESTS[body["testRunId"]][(page - 1) * size : page * size]
            return httpx.Response(200, json={"results": tests})
        return httpx.Response(404, json={"status": 404, "error": "Not Found"})

    return httpx.MockTransport(handler)


def test_import_from_an_older_zebrunner(app, caplog):
    calls = []
    zebrunner = Zebrunner(ZE, "refresh", transport=fake_legacy_zebrunner(calls))
    caplog.set_level("INFO", logger="zebrunner-import")
    stats = import_runs(zebrunner, _radiator(app), env="ci", workers=1)

    assert zebrunner.api == "legacy"
    assert stats.imported == 2 and not stats.failed
    # the reports come from the legacy logs, paged by number
    assert stats.with_report == 1  # the one test with a logged report
    assert "doesn't serve test logs" not in caplog.text
    assert "Zebrunner legacy launch fields: elapsed, id, startedAt, testSuite" in (
        caplog.text
    )
    assert any(path == "/api/reporting/api/tests/search" for _, path, _ in calls)

    with app.state.sessionmaker() as session:
        latest = queries.get_run(session, run_id_for(ZE, 101))
        assert latest.suite == "sprint_4_tests"
        assert latest.started_at == datetime(2025, 3, 2, 6, tzinfo=timezone.utc)
        # elapsed (seconds) gives the end
        assert (latest.ended_at - latest.started_at).total_seconds() == 3000
        assert {a.asset_id for a in latest.assets} == {"Asset_1", "Asset_2"}
        one = next(a for a in latest.assets if a.asset_id == "Asset_1")
        ars = next(a for a in one.agents if a.agent == "ars")
        assert (ars.rank, ars.pk) == (3, "merged-1")


def test_an_older_zebrunner_without_logs_imports_without_reports(app, caplog):
    zebrunner = Zebrunner(
        ZE, "refresh", transport=fake_legacy_zebrunner([], logs=False)
    )
    stats = import_runs(zebrunner, _radiator(app), env="ci", workers=1)
    assert stats.imported == 2 and not stats.failed
    assert stats.with_report == 0
    assert caplog.text.count("doesn't serve test logs") == 1


def test_the_api_can_be_chosen():
    calls = []
    zebrunner = Zebrunner(
        ZE, "refresh", transport=fake_legacy_zebrunner(calls), api="legacy"
    )
    assert [launch["id"] for launch in zebrunner.launches()] == [101, 100, 99]
    # straight to the legacy API, never trying v1
    assert all("/v1/launches" not in path for _, path, _ in calls)


# --- environments from the ARS -------------------------------------------------


def fake_ars(known: dict, down=()):
    """ARSs by host: `known` maps a host to the pks it has; hosts in `down`
    can't be reached."""

    def handler(request: httpx.Request):
        host = request.url.host
        if host in down:
            raise httpx.ConnectError("Connection refused", request=request)
        pk = request.url.path.rsplit("/", 1)[-1]
        if pk in known.get(host, ()):
            return httpx.Response(200, json={"message": pk, "status": "Done"})
        return httpx.Response(404, json={"error": "not found"})

    return httpx.MockTransport(handler)


HOSTS = {
    "dev": "https://ars.dev.example",
    "ci": "https://ars.ci.example",
    "test": "https://ars.test.example",
}


def test_report_pks_prefer_the_parent():
    reports = {
        1: {"pks": {"ars": "merged-1", "parent_pk": "parent-1"}},
        2: {"pks": {"parent_pk": "parent-2", "aragorn": "child-2"}},
        3: {"pks": {}},
    }
    assert report_pks(reports) == ["parent-1", "parent-2", "merged-1"]


def test_a_runs_environment_comes_from_the_ars_that_has_its_pk(app, caplog):
    caplog.set_level("INFO", logger="zebrunner-import")
    resolver = ArsEnvResolver(
        HOSTS, transport=fake_ars({"ars.ci.example": {"parent-1"}})
    )
    stats = import_runs(
        _zebrunner([]), _radiator(app), workers=1, env_resolver=resolver
    )
    assert stats.env_from_pk == 1
    assert (
        "launch 101 (sprint_4_tests: 2025_03_02_06_00): pk parent-1 is on the ci ARS"
        in (caplog.text)
    )
    with app.state.sessionmaker() as session:
        assert queries.get_run(session, run_id_for(ZE, 101)).env == "ci"
        # no logged report, so no pk: back to the agents (aragorn is prod-only)
        assert queries.get_run(session, run_id_for(ZE, 100)).env == "prod"
    assert stats.envs == {"ci": 1, "prod": 1}


def test_pks_no_ars_has_fall_back(app, caplog):
    caplog.set_level("INFO", logger="zebrunner-import")
    resolver = ArsEnvResolver(HOSTS, transport=fake_ars({}))
    stats = import_runs(
        _zebrunner([]), _radiator(app), workers=1, env_resolver=resolver
    )
    assert stats.env_from_pk == 0
    assert "none of its pks are on any ARS" in caplog.text
    with app.state.sessionmaker() as session:
        assert queries.get_run(session, run_id_for(ZE, 101)).env == "prod"


def test_an_unreachable_ars_is_skipped_after_one_warning(caplog):
    resolver = ArsEnvResolver(
        HOSTS,
        transport=fake_ars(
            {"ars.test.example": {"p1", "p2"}}, down={"ars.dev.example"}
        ),
    )
    assert resolver.env_for({1: {"pks": {"parent_pk": "p1"}}}) == ("test", "p1")
    assert resolver.env_for({1: {"pks": {"parent_pk": "p2"}}}) == ("test", "p2")
    assert caplog.text.count("Can't reach the dev ARS") == 1


def test_ars_hosts_come_from_smartapi():
    registry = {
        "hits": [
            {
                "info": {"x-translator": {"infores": "infores:ars"}},
                "servers": [
                    {"url": "https://ars-dev.example/", "x-maturity": "development"},
                    {"url": "https://ars.ci.example", "x-maturity": "staging"},
                    {"url": "https://ars.test.example", "x-maturity": "testing"},
                    {"url": "https://ars-prod.example", "x-maturity": "production"},
                ],
            },
            {
                "info": {"x-translator": {"infores": "infores:aragorn"}},
                "servers": [
                    {"url": "https://aragorn.example", "x-maturity": "staging"}
                ],
            },
        ]
    }
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=registry))
    )
    assert ars_hosts_from_smartapi(client) == {
        "dev": "https://ars-dev.example",
        "ci": "https://ars.ci.example",
        "test": "https://ars.test.example",
        "prod": "https://ars-prod.example",
    }
