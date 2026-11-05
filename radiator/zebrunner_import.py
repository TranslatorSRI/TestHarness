"""Import past runs from Zebrunner into the Information Radiator.

    python -m radiator.zebrunner_import \\
        --zebrunner-url https://zebrunner.example.org --refresh-token ... \\
        --radiator-url https://radiator.example.org --radiator-token ... \\
        [--project-id 1] [--env ci] [--since 2025-01-01] [--dry-run]

Reads Zebrunner's own read API (the same service the harness reports to).
Zebrunner changed it between versions, so the import speaks both (``--api``;
by default it tries the newer one and falls back):

* v1 (reporting-service 1.4x and later): ``GET /api/reporting/v1/launches``
  (needs ``--project-id``), ``GET .../v1/launches/{id}/tests``, and a test's
  logs from ``GET .../v1/test-runs/{id}/tests/{testId}/logs-and-screenshots``.
* legacy (eg 1.28): ``GET /api/reporting/api/tests/runs/search``,
  ``POST /api/reporting/api/tests/search``, and a test's logs from
  ``GET .../v1/test-runs/{id}/tests/{testId}/logs?page=&pageSize=``, as
  Zebrunner's own UI calls them.

and posts each run to the radiator's ingest API, exactly as the harness does.

What comes across, and from where:

* From the test labels the harness set: the test case and asset ids, the
  expected output, the CURIEs, and each agent's status.
* From the per-test report the harness logged (the JSON ``TestReport``): each
  agent's message, whether the expected answer was found, its rank and score,
  and the pks. ``--no-logs`` skips fetching these, which is much faster.
* Not available at all: the number of results, HTTP status and response time
  (never recorded), and the environment (never sent to Zebrunner; see
  ``--env``).

Imported runs are marked ``origin=zebrunner_import`` and get a run id derived
from the Zebrunner launch id, so re-running the import updates them instead of
duplicating them. Launches already imported in full are skipped unless
``--force``; one whose import stopped partway is imported again.
Performance tests are skipped: they predate HelmsDeep and carry no summary.
"""

import argparse
import json
import logging
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator, Optional

import httpx

import radiator_schema as schema

logger = logging.getLogger("zebrunner-import")

# Labels the harness set on every test; any other label key is an agent.
META_LABELS = {
    "TestCase",
    "TestAsset",
    "ExpectedOutput",
    "InputCurie",
    "OutputCurie",
    "SourceInputCurie",
    "TargetInputCurie",
    "TestRunTime",
    "UserSpawnRate",
}
PERFORMANCE_LABELS = {"TestRunTime", "UserSpawnRate"}
STATUSES = {s.value for s in schema.Status}
# Zebrunner's own test statuses, for tests without an agent label to go by.
# It has no NO_RESULTS or ERROR, so the harness's attempts to finish a test
# with those were refused and the test stayed IN_PROGRESS.
ZEBRUNNER_STATUS = {
    "PASSED": "PASSED",
    "FAILED": "FAILED",
    "SKIPPED": "SKIPPED",
    "ABORTED": "ERROR",
}
# Agents that only exist in prod; the shepherd-* agents are dev/ci/test.
PROD_AGENTS = {
    "aragorn",
    "arax",
    "biothings-explorer",
    "improving-agent",
    "unsecret-agent",
    "cqs",
}
IMPORT_NAMESPACE = uuid.UUID("6f1c8a52-1d7e-4a8f-9a55-0b7c3f6d2e11")


# v1, read from Zebrunner's images (reporting-service 1.54.1
# LaunchController, test-execution-logs-service 1.6.1 LogController)
LAUNCHES_PATH = "/api/reporting/v1/launches"
TESTS_PATH = "/api/reporting/v1/launches/{launch_id}/tests"
LOGS_PATH = (
    "/api/reporting/v1/test-runs/{launch_id}/tests/{test_id}/logs-and-screenshots"
)
# legacy, as Zebrunner 1.28's UI calls it
LEGACY_LAUNCHES_PATH = "/api/reporting/api/tests/runs/search"
LEGACY_TESTS_PATH = "/api/reporting/api/tests/search"
LEGACY_LOGS_PATH = "/api/reporting/v1/test-runs/{launch_id}/tests/{test_id}/logs"
V1, LEGACY, AUTO = "v1", "legacy", "auto"


class ZebrunnerError(RuntimeError):
    """Zebrunner answered with an error; the message has its answer."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


def run_id_for(zebrunner_url: str, launch_id) -> uuid.UUID:
    """Stable per launch, so re-importing updates rather than duplicates."""
    return uuid.uuid5(IMPORT_NAMESPACE, f"{zebrunner_url.rstrip('/')}/{launch_id}")


def parse_time(value) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        # epoch millis
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    text = str(value).replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def suite_from_name(name: str) -> str:
    """The harness named runs ``"<suite>: <YYYY_MM_DD_HH_MM>"``."""
    suite, sep, _ = (name or "").rpartition(":")
    return suite.strip() if sep and suite.strip() else (name or "unknown").strip()


# --- Zebrunner client --------------------------------------------------------


def _page_items(payload: dict) -> list:
    """A page's items: ``items`` in v1, ``results`` in the legacy API."""
    if not isinstance(payload, dict):
        return []
    items = payload.get("items")
    if items is None:
        items = payload.get("results")
    return items or []


def legacy_launch(item: dict) -> dict:
    """A legacy test run, with the fields the import reads under their v1
    names. The legacy API names and nests some of them differently."""
    suite = item.get("testSuite") or {}
    started = item.get("startedAt") or item.get("startTime") or item.get("createdAt")
    ended = item.get("endedAt") or item.get("finishTime")
    if ended is None and isinstance(started, (int, float)) and item.get("elapsed"):
        ended = started + item["elapsed"] * 1000  # elapsed is in seconds
    return {
        **item,
        "name": item.get("name") or suite.get("name") or item.get("testSuiteName"),
        "startedAt": started,
        "endedAt": ended,
        "build": item.get("build") or (item.get("config") or {}).get("build"),
    }


class Zebrunner:
    """The handful of Zebrunner read calls the import needs, in either of its
    API generations."""

    def __init__(
        self,
        base_url: str,
        refresh_token: str,
        project_id: Optional[int] = None,
        transport: Optional[httpx.BaseTransport] = None,
        api: str = AUTO,
    ):
        self.base_url = base_url.rstrip("/")
        self.refresh_token = refresh_token
        self.project_id = project_id
        self.api = api
        self.client = httpx.Client(
            base_url=self.base_url, timeout=60, transport=transport
        )
        # turned off for the rest of the import by the first logs request
        # Zebrunner can't answer, so that's one warning, not one per test
        self.logs_available = True
        self._shown_fields = set()
        self._authenticate()

    def _authenticate(self):
        res = self.client.post(
            "/api/iam/v1/auth/refresh", json={"refreshToken": self.refresh_token}
        )
        res.raise_for_status()
        self.client.headers["Authorization"] = f"Bearer {res.json()['authToken']}"

    def _request(self, method: str, path: str, params=None, body=None) -> dict:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        if self.project_id is not None:
            params.setdefault("projectId", self.project_id)
        res = self.client.request(method, path, params=params, json=body)
        if res.status_code == 401:
            # access tokens are short-lived; an import can outlast one
            self._authenticate()
            res = self.client.request(method, path, params=params, json=body)
        if res.is_error:
            # say what Zebrunner said, not just the status: a 400 names the
            # parameter it wanted
            hint = ""
            if (
                res.status_code in (400, 404)
                and self.project_id is None
                and "/v1/launches" in path
            ):
                hint = " (Zebrunner's v1 API needs the project: pass --project-id)"
            raise ZebrunnerError(
                f"{method} {res.request.url} answered {res.status_code}: "
                f"{res.text[:500]}{hint}",
                res.status_code,
            )
        return res.json()

    def _get(self, path: str, **params) -> dict:
        return self._request("GET", path, params)

    def _show_fields(self, what: str, item: dict):
        """Log the fields of the first item of each kind, once: if the
        legacy API names something differently, the dry run shows it."""
        if what not in self._shown_fields and isinstance(item, dict):
            self._shown_fields.add(what)
            logger.info(f"Zebrunner {what} fields: {', '.join(sorted(item))}")

    def _paged(self, fetch, page_size: int) -> Iterator[dict]:
        page = 1
        seen = set()
        while True:
            items = fetch(page, page_size)
            fresh = [item for item in items if item.get("id") not in seen]
            if not fresh:
                return
            for item in fresh:
                seen.add(item.get("id"))
                yield item
            if len(items) < page_size:
                return
            page += 1

    def launches(self, page_size: int = 50) -> Iterator[dict]:
        if self.api in (V1, AUTO):
            try:
                first = self._get(LAUNCHES_PATH, page=1, pageSize=page_size)
            except ZebrunnerError as e:
                if self.api == V1 or e.status_code != 404:
                    raise
                logger.info(
                    "Zebrunner has no /v1/launches (an older Zebrunner, or a "
                    "newer one without --project-id); using its legacy API"
                )
                self.api = LEGACY
            else:
                self.api = V1
                yield from self._paged(
                    lambda page, size: (
                        _page_items(first)
                        if page == 1
                        else _page_items(
                            self._get(LAUNCHES_PATH, page=page, pageSize=size)
                        )
                    ),
                    page_size,
                )
                return
        for item in self._paged(
            lambda page, size: _page_items(
                self._get(LEGACY_LAUNCHES_PATH, page=page, pageSize=size)
            ),
            page_size,
        ):
            self._show_fields("legacy launch", item)
            yield legacy_launch(item)

    def tests(self, launch_id, page_size: int = 500) -> list[dict]:
        if self.api == V1:
            return _page_items(self._get(TESTS_PATH.format(launch_id=launch_id)))
        tests = list(
            self._paged(
                lambda page, size: _page_items(
                    self._request(
                        "POST",
                        LEGACY_TESTS_PATH,
                        body={"testRunId": launch_id, "page": page, "pageSize": size},
                    )
                ),
                page_size,
            )
        )
        if tests:
            self._show_fields("legacy test", tests[0])
        return tests

    def _legacy_logs(self, launch_id, test_id, page_size: int = 1000) -> list[dict]:
        """Paged by number (``page``, ``totalPages``); entries have no id."""
        logs, page = [], 1
        while True:
            payload = self._get(
                LEGACY_LOGS_PATH.format(launch_id=launch_id, test_id=test_id),
                page=page,
                pageSize=page_size,
            )
            items = _page_items(payload)
            logs.extend(items)
            pages = payload.get("totalPages")
            if not items or (pages is not None and page >= pages):
                return logs
            if pages is None and len(items) < page_size:
                return logs
            page += 1

    def logs(self, launch_id, test_id, page_size: int = 100) -> list[dict]:
        """A test's log lines; [] once Zebrunner turns out not to serve them."""
        if not self.logs_available:
            return []
        logs, token = [], None
        try:
            if self.api == LEGACY:
                return self._legacy_logs(launch_id, test_id)
            while True:
                payload = self._get(
                    LOGS_PATH.format(launch_id=launch_id, test_id=test_id),
                    maxPageSize=page_size,
                    pageToken=token,
                )
                logs.extend(_page_items(payload))
                token = (payload.get("_meta") or {}).get("nextPageToken")
                if not token or not _page_items(payload):
                    return logs
        except ZebrunnerError as e:
            if e.status_code not in (404, 405):
                raise
            self.logs_available = False
            logger.warning(
                f"This Zebrunner doesn't serve test logs where the import looks "
                f"({str(e)[:300]}); importing without ranks, scores and pks "
                "(as with --no-logs)."
            )
            return []


# --- environments, from the ARS ----------------------------------------------

SMARTAPI_URL = "https://smart-api.info/api/query?limit=1000&q=TRAPI"
# SmartAPI's x-maturity for each environment, as the harness maps them
MATURITY_ENV = {
    "development": "dev",
    "staging": "ci",
    "testing": "test",
    "production": "prod",
}


def ars_hosts_from_smartapi(client: httpx.Client) -> dict[str, str]:
    """Each environment's ARS, from the SmartAPI registry (as the harness
    finds it)."""
    res = client.get(SMARTAPI_URL)
    res.raise_for_status()
    hosts: dict[str, str] = {}
    for hit in res.json().get("hits", []):
        infores = (hit.get("info") or {}).get("x-translator", {}).get("infores")
        if infores != "infores:ars":
            continue
        for server in hit.get("servers") or []:
            env = MATURITY_ENV.get(server.get("x-maturity"))
            if env and server.get("url"):
                hosts.setdefault(env, server["url"].rstrip("/"))
    return hosts


def report_pks(reports: dict, limit: int = 3) -> list[str]:
    """A few distinct pks from a launch's reports, parent pks first."""
    parents, others = [], []
    for report in reports.values():
        pks = report.get("pks") or {}
        for key, pk in pks.items():
            if not isinstance(pk, str) or not pk:
                continue
            (parents if key == "parent_pk" else others).append(pk)
    seen, out = set(), []
    for pk in parents + others:
        if pk not in seen:
            seen.add(pk)
            out.append(pk)
        if len(out) == limit:
            break
    return out


class ArsEnvResolver:
    """Which environment a launch ran in, from where its pks live: a pk only
    exists on the ARS that made it, and the harness asked the ARS to retain
    every response."""

    def __init__(
        self,
        hosts: dict[str, str],
        transport: Optional[httpx.BaseTransport] = None,
        timeout: float = 30,
    ):
        self.hosts = dict(hosts)
        self.client = httpx.Client(
            timeout=timeout, transport=transport, follow_redirects=True
        )
        self.unreachable: set[str] = set()
        self._last: Optional[str] = None

    def _has(self, env: str, pk: str) -> bool:
        url = f"{self.hosts[env]}/ars/api/messages/{pk}"
        try:
            res = self.client.get(url, params={"trace": "y"})
        except httpx.HTTPError as e:
            self.unreachable.add(env)
            logger.warning(
                f"Can't reach the {env} ARS at {self.hosts[env]} ({e}); not "
                "looking pks up there for the rest of the import."
            )
            return False
        if res.status_code != 200:
            return False
        try:
            return isinstance(res.json(), dict)
        except ValueError:
            return False

    def env_for(self, reports: dict) -> tuple[Optional[str], Optional[str]]:
        """(environment, the pk that showed it), or (None, None)."""
        for pk in report_pks(reports):
            # runs come in order, and neighbours mostly share an environment
            order = sorted(self.hosts, key=lambda env: env != self._last)
            for env in order:
                if env not in self.unreachable and self._has(env, pk):
                    self._last = env
                    return env, pk
        return None, None


# --- conversion ----------------------------------------------------------------


@dataclass
class ImportStats:
    launches: int = 0
    imported: int = 0
    skipped_existing: int = 0
    skipped_empty: int = 0
    assets: int = 0
    with_report: int = 0
    performance_tests_skipped: int = 0
    unknown_env: int = 0
    env_from_pk: int = 0
    envs: dict = field(default_factory=dict)
    failed: list = field(default_factory=list)


def labels_of(test: dict) -> dict[str, str]:
    labels = {}
    for label in test.get("labels") or []:
        if isinstance(label, dict) and label.get("key") is not None:
            labels[str(label["key"])] = (
                None if label.get("value") is None else str(label["value"])
            )
    return labels


def find_report(logs: list[dict]) -> Optional[dict]:
    """The harness's TestReport: the last log line that parses as one."""
    for log in reversed(logs):
        try:
            data = json.loads(log.get("message") or "")
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("result"), dict):
            return data
    return None


def _number(value, kind):
    try:
        return None if value is None else kind(value)
    except (TypeError, ValueError):
        return None


def expected_answer(agent_report: dict) -> tuple:
    """(found, rank, score), as the harness's collector reads them."""
    actual = agent_report.get("actual_output")
    if not isinstance(actual, dict) or not actual:
        status = agent_report.get("status")
        return (False if status == "NO_RESULTS" else None), None, None
    rank = actual.get("ars_rank")
    if rank is None:
        rank = actual.get("ara_rank")
    score = actual.get("ars_score")
    if score is None:
        score = actual.get("ara_score")
    found = actual.get("found")
    if found is None:
        found = rank is not None or score is not None
    return bool(found), _number(rank, int), _number(score, float)


def convert_test(test: dict, report: Optional[dict]) -> Optional[schema.AssetResult]:
    labels = labels_of(test)
    if PERFORMANCE_LABELS & labels.keys():
        return None
    agents_status = {
        key: value
        for key, value in labels.items()
        if key not in META_LABELS and value in STATUSES
    }
    report_agents = (report or {}).get("result") or {}
    pks = (report or {}).get("pks") or {}

    agents = []
    for agent in sorted(set(agents_status) | set(report_agents)):
        detail = report_agents.get(agent) or {}
        status = agents_status.get(agent) or detail.get("status")
        if status not in STATUSES:
            continue
        found, rank, score = expected_answer({**detail, "status": status})
        pk = pks.get(agent)
        agents.append(
            schema.AgentResult(
                agent=agent,
                status=status,
                message=detail.get("message"),
                found=found,
                rank=rank,
                score=score,
                pk=None if pk in (None, "", "None") else str(pk),
                expected_nodes_found=detail.get("expected_nodes_found"),
            )
        )

    # the overall status the harness gave the test: the ARS's, or the single
    # override agent's; Zebrunner's own status only as a fallback
    by_agent = {a.agent: a.status for a in agents}
    if "ars" in by_agent:
        status = by_agent["ars"]
    elif len(by_agent) == 1:
        status = next(iter(by_agent.values()))
    else:
        status = ZEBRUNNER_STATUS.get(test.get("status"), "SKIPPED")

    pathfinder = "SourceInputCurie" in labels
    parent_pk = pks.get("parent_pk")
    return schema.AssetResult(
        test_case_id=labels.get("TestCase") or test.get("testClass") or "unknown",
        asset_id=labels.get("TestAsset") or f"zebrunner-test-{test.get('id')}",
        kind="pathfinder" if pathfinder else "acceptance",
        name=test.get("name"),
        expected_output=labels.get("ExpectedOutput"),
        input_curie=labels.get("SourceInputCurie" if pathfinder else "InputCurie"),
        # the harness comma-separates an asset's several output CURIEs
        output_curies=[
            curie
            for curie in (
                labels.get("TargetInputCurie" if pathfinder else "OutputCurie") or ""
            ).split(",")
            if curie
        ],
        status=status,
        parent_pk=None if parent_pk in (None, "", "None") else str(parent_pk),
        agents=agents,
        details=(report or {}).get("test_details") or {},
    )


def infer_env(results: list[schema.AssetResult]) -> Optional[str]:
    agents = {a.agent for r in results for a in r.agents}
    if agents & PROD_AGENTS:
        return "prod"
    return None


def convert_launch(
    zebrunner_url: str,
    launch: dict,
    tests: list[dict],
    reports: dict,
    env: Optional[str],
    stats: ImportStats,
) -> Optional[schema.RunPayload]:
    results = []
    for test in tests:
        result = convert_test(test, reports.get(test.get("id")))
        if result is None:
            stats.performance_tests_skipped += 1
            continue
        results.append(result)
    if not results:
        return None

    # the same asset can appear twice if a test was retried; keep the last
    deduped = {(r.test_case_id, r.asset_id): r for r in results}
    results = list(deduped.values())

    started_at = parse_time(launch.get("startedAt")) or datetime.now(timezone.utc)
    ended_at = parse_time(launch.get("endedAt"))
    run_env = env or infer_env(results)
    if run_env is None:
        stats.unknown_env += 1
    counts: dict = {}
    for result in results:
        counts[result.status.value] = counts.get(result.status.value, 0) + 1

    return schema.RunPayload(
        run=schema.RunCreate(
            run_id=run_id_for(zebrunner_url, launch["id"]),
            suite=suite_from_name(launch.get("name")),
            env=run_env,
            harness_version=launch.get("build"),
            started_at=started_at,
            origin="zebrunner_import",
            origin_ref=str(launch["id"]),
        ),
        results=results,
        finish=(
            schema.RunFinish(ended_at=ended_at, counts=counts) if ended_at else None
        ),
    )


# --- radiator side ---------------------------------------------------------------


class Radiator:
    def __init__(self, base_url: str, token: str, transport=None):
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=60,
            transport=transport,
        )

    def has_run(self, run_id: uuid.UUID) -> bool:
        """Whether the run was imported in full.

        One that exists but never got its end time stopped partway (eg an
        upload failed), so it's imported again; the upserts make that safe.
        """
        res = self.client.get(f"/api/runs/{run_id}", params={"results": "false"})
        if res.status_code == 404:
            return False
        res.raise_for_status()
        return res.json().get("ended_at") is not None

    def push(self, payload: schema.RunPayload, batch_size: int = 200) -> None:
        prefix = schema.INGEST_PREFIX
        run_id = payload.run.run_id
        self.client.post(
            f"{prefix}/runs", json=payload.run.model_dump(mode="json")
        ).raise_for_status()
        for i in range(0, len(payload.results), batch_size):
            batch = schema.ResultBatch(results=payload.results[i : i + batch_size])
            self.client.post(
                f"{prefix}/runs/{run_id}/results", json=batch.model_dump(mode="json")
            ).raise_for_status()
        if payload.finish is not None:
            self.client.patch(
                f"{prefix}/runs/{run_id}", json=payload.finish.model_dump(mode="json")
            ).raise_for_status()


# --- the import ------------------------------------------------------------------


def import_runs(
    zebrunner: Zebrunner,
    radiator: Optional[Radiator],
    *,
    env: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    suite: Optional[str] = None,
    fetch_logs: bool = True,
    force: bool = False,
    workers: int = 8,
    limit: Optional[int] = None,
    env_resolver: Optional[ArsEnvResolver] = None,
) -> ImportStats:
    """Import every matching launch. ``radiator=None`` is a dry run."""
    stats = ImportStats()
    selected = []
    for launch in zebrunner.launches():
        started = parse_time(launch.get("startedAt"))
        if since and started and started < since:
            continue
        if until and started and started >= until:
            continue
        if suite and suite_from_name(launch.get("name")) != suite:
            continue
        if limit is not None and len(selected) >= limit:
            break
        selected.append(launch)
    # Oldest first: the radiator numbers runs as they arrive, so this keeps
    # imported runs' numbers in the order they ran.
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    selected.sort(key=lambda launch: parse_time(launch.get("startedAt")) or epoch)
    for launch in selected:
        stats.launches += 1
        name = f"launch {launch.get('id')} ({launch.get('name')})"
        run_id = run_id_for(zebrunner.base_url, launch["id"])
        try:
            if radiator is not None and not force and radiator.has_run(run_id):
                stats.skipped_existing += 1
                logger.info(f"{name}: already imported, skipping")
                continue
            tests = zebrunner.tests(launch["id"])
            reports = {}
            if fetch_logs and tests:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    found = pool.map(
                        lambda t: (
                            t.get("id"),
                            find_report(zebrunner.logs(launch["id"], t.get("id"))),
                        ),
                        tests,
                    )
                    reports = {test_id: report for test_id, report in found if report}
            run_env = env
            if run_env is None and env_resolver is not None and reports:
                run_env, pk = env_resolver.env_for(reports)
                if run_env:
                    stats.env_from_pk += 1
                    logger.info(f"{name}: pk {pk} is on the {run_env} ARS")
                else:
                    logger.info(f"{name}: none of its pks are on any ARS")
            payload = convert_launch(
                zebrunner.base_url, launch, tests, reports, run_env, stats
            )
            if payload is None:
                stats.skipped_empty += 1
                logger.info(f"{name}: no acceptance results, skipping")
                continue
            run_env_name = payload.run.env or "unknown"
            stats.envs[run_env_name] = stats.envs.get(run_env_name, 0) + 1
            stats.assets += len(payload.results)
            stats.with_report += sum(1 for t in tests if t.get("id") in reports)
            if radiator is not None:
                radiator.push(payload)
            stats.imported += 1
            logger.info(
                f"{name}: {'would import' if radiator is None else 'imported'} "
                f"{len(payload.results)} assets ({payload.run.suite}, env "
                f"{payload.run.env or 'unknown'})"
            )
        except Exception as e:
            stats.failed.append((launch.get("id"), str(e)))
            logger.error(f"{name}: failed: {e}")
    return stats


def make_env_resolver(overrides: list[str]) -> ArsEnvResolver:
    """An ArsEnvResolver for every environment: SmartAPI's ARS hosts, with
    ``ENV=URL`` overrides on top."""
    hosts: dict[str, str] = {}
    try:
        with httpx.Client(timeout=30) as client:
            hosts = ars_hosts_from_smartapi(client)
    except httpx.HTTPError as e:
        if not overrides:
            raise
        logger.warning(f"Couldn't read the SmartAPI registry ({e}); using --ars")
    for spec in overrides:
        env, sep, url = spec.partition("=")
        if not sep or not env.strip() or not url.strip():
            raise ValueError(f"--ars wants ENV=URL, got {spec!r}")
        hosts[env.strip()] = url.strip().rstrip("/")
    if not hosts:
        raise ValueError("no ARS found in the SmartAPI registry; pass --ars")
    logger.info(
        "Looking runs' environments up on: "
        + ", ".join(f"{env} {url}" for env, url in sorted(hosts.items()))
    )
    return ArsEnvResolver(hosts)


def _date(text: str) -> datetime:
    return parse_time(text if "T" in text else f"{text}T00:00:00+00:00")


def cli(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Import past runs from Zebrunner into the Information Radiator."
    )
    parser.add_argument("--zebrunner-url", required=True)
    parser.add_argument(
        "--refresh-token",
        required=True,
        help="Zebrunner API refresh token (ZE_REFRESH_TOKEN)",
    )
    parser.add_argument(
        "--project-id",
        type=int,
        help=(
            "Zebrunner project id, which newer Zebrunners (the v1 API) need to "
            "list launches: the projectId in the API calls its launches page "
            "makes"
        ),
    )
    parser.add_argument(
        "--api",
        choices=[AUTO, V1, LEGACY],
        default=AUTO,
        help=(
            "Which Zebrunner API to read: v1 (reporting-service 1.4x and "
            "later), legacy (eg 1.28), or auto (try v1, fall back to legacy)"
        ),
    )
    parser.add_argument(
        "--radiator-url", help="Where to import to (omit with --dry-run)"
    )
    parser.add_argument("--radiator-token", help="The radiator's API token")
    parser.add_argument(
        "--env",
        help=(
            "Environment to record every imported run under. Zebrunner never "
            "stored it; without this, each run's is looked up from its pks "
            "(see --ars), and failing that, runs with prod-only agents are "
            "marked prod and the rest have no environment."
        ),
    )
    parser.add_argument(
        "--ars",
        action="append",
        default=[],
        metavar="ENV=URL",
        help=(
            "An environment's ARS, for looking runs' pks up, eg "
            "ci=https://ars.ci.transltr.io. Repeatable. Defaults to the ARS "
            "of each environment in the SmartAPI registry."
        ),
    )
    parser.add_argument(
        "--no-env-lookup",
        action="store_true",
        help="Don't look runs' pks up on the ARSs to find their environment",
    )
    parser.add_argument("--suite", help="Only import runs of this suite")
    parser.add_argument(
        "--since", type=_date, help="Only runs started on/after this date (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--until", type=_date, help="Only runs started before this date"
    )
    parser.add_argument("--limit", type=int, help="Stop after this many runs")
    parser.add_argument(
        "--no-logs",
        action="store_true",
        help="Skip per-test logs: no ranks, scores or pks, but much faster",
    )
    parser.add_argument(
        "--force", action="store_true", help="Re-import runs that were already imported"
    )
    parser.add_argument(
        "--workers", type=int, default=8, help="Parallel log requests per run"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Read and convert, but write nothing"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if not args.dry_run and not (args.radiator_url and args.radiator_token):
        parser.error(
            "--radiator-url and --radiator-token are required unless --dry-run"
        )

    zebrunner = Zebrunner(
        args.zebrunner_url, args.refresh_token, args.project_id, api=args.api
    )
    radiator = (
        None if args.dry_run else Radiator(args.radiator_url, args.radiator_token)
    )
    env_resolver = None
    if args.env is None and not args.no_env_lookup and not args.no_logs:
        # the pks come from the logged reports, so no logs, no lookup
        try:
            env_resolver = make_env_resolver(args.ars)
        except (ValueError, httpx.HTTPError) as e:
            logger.error(f"Can't look environments up: {e}")
            return 1
    try:
        stats = import_runs(
            zebrunner,
            radiator,
            env=args.env,
            since=args.since,
            until=args.until,
            suite=args.suite,
            fetch_logs=not args.no_logs,
            force=args.force,
            workers=args.workers,
            limit=args.limit,
            env_resolver=env_resolver,
        )
    except (ZebrunnerError, httpx.HTTPError) as e:
        # eg the login or the list of launches: nothing was imported
        logger.error(f"Couldn't read from Zebrunner: {e}")
        return 1
    logger.info(
        f"Done: {stats.imported} of {stats.launches} runs "
        f"{'would be ' if args.dry_run else ''}imported, {stats.assets} assets "
        f"({stats.with_report} with a logged report). Skipped: "
        f"{stats.skipped_existing} already imported, {stats.skipped_empty} "
        f"without acceptance results, {stats.performance_tests_skipped} "
        f"performance tests. {stats.unknown_env} runs have no environment."
    )
    if stats.envs:
        envs = ", ".join(f"{env} {n}" for env, n in sorted(stats.envs.items()))
        how = (
            f" ({stats.env_from_pk} found from their pks on the ARS)"
            if env_resolver is not None
            else ""
        )
        logger.info(f"Environments: {envs}{how}")
    for launch_id, error in stats.failed:
        logger.error(f"Failed: launch {launch_id}: {error}")
    return 1 if stats.failed else 0


if __name__ == "__main__":
    sys.exit(cli())
