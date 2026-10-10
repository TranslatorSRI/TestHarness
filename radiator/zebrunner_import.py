"""Import past runs from Zebrunner into the Information Radiator.

    python -m radiator.zebrunner_import \\
        --zebrunner-url https://zebrunner.example.org --refresh-token ... \\
        --radiator-url https://radiator.example.org --radiator-token ... \\
        [--project-id 1] [--env ci] [--since 2025-01-01] [--dry-run]

Reads Zebrunner's own read API (the same service the harness reports to):

* ``GET /api/reporting/v1/launches`` - runs ("launches"), paged
* ``GET /api/reporting/v1/launches/{id}/tests`` - a run's tests, with labels
* ``GET /api/reporting/v1/test-runs/{id}/tests/{testId}/logs`` - a test's logs

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
from typing import Any, Iterator, Optional

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


class Zebrunner:
    """The handful of Zebrunner read calls the import needs."""

    def __init__(
        self,
        base_url: str,
        refresh_token: str,
        project_id: Optional[int] = None,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.refresh_token = refresh_token
        self.project_id = project_id
        self.client = httpx.Client(
            base_url=self.base_url, timeout=60, transport=transport
        )
        self._authenticate()

    def _authenticate(self):
        res = self.client.post(
            "/api/iam/v1/auth/refresh", json={"refreshToken": self.refresh_token}
        )
        res.raise_for_status()
        self.client.headers["Authorization"] = f"Bearer {res.json()['authToken']}"

    def _get(self, path: str, **params) -> dict:
        params = {k: v for k, v in params.items() if v is not None}
        if self.project_id is not None:
            params.setdefault("projectId", self.project_id)
        res = self.client.get(path, params=params)
        if res.status_code == 401:
            # access tokens are short-lived; an import can outlast one
            self._authenticate()
            res = self.client.get(path, params=params)
        res.raise_for_status()
        return res.json()

    def launches(self, page_size: int = 50) -> Iterator[dict]:
        page = 1
        seen = set()
        while True:
            items = self._get(
                "/api/reporting/v1/launches", page=page, pageSize=page_size
            ).get("items", [])
            fresh = [item for item in items if item.get("id") not in seen]
            if not fresh:
                return
            for item in fresh:
                seen.add(item.get("id"))
                yield item
            if len(items) < page_size:
                return
            page += 1

    def tests(self, launch_id) -> list[dict]:
        return self._get(f"/api/reporting/v1/launches/{launch_id}/tests").get(
            "items", []
        )

    def logs(self, launch_id, test_id, page_size: int = 100) -> list[dict]:
        logs, token = [], None
        while True:
            payload = self._get(
                f"/api/reporting/v1/test-runs/{launch_id}/tests/{test_id}/logs",
                maxPageSize=page_size,
                pageToken=token,
            )
            logs.extend(payload.get("items", []))
            token = (payload.get("_meta") or {}).get("nextPageToken")
            if not token or not payload.get("items"):
                return logs


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
        output_curie=labels.get("TargetInputCurie" if pathfinder else "OutputCurie"),
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
            payload = convert_launch(
                zebrunner.base_url, launch, tests, reports, env, stats
            )
            if payload is None:
                stats.skipped_empty += 1
                logger.info(f"{name}: no acceptance results, skipping")
                continue
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
        help="Zebrunner project id, if your instance needs one",
    )
    parser.add_argument(
        "--radiator-url", help="Where to import to (omit with --dry-run)"
    )
    parser.add_argument("--radiator-token", help="The radiator's API token")
    parser.add_argument(
        "--env",
        help=(
            "Environment to record the imported runs under. Zebrunner never "
            "stored it; without this, runs with prod agents are marked prod "
            "and the rest have no environment."
        ),
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

    zebrunner = Zebrunner(args.zebrunner_url, args.refresh_token, args.project_id)
    radiator = (
        None if args.dry_run else Radiator(args.radiator_url, args.radiator_token)
    )
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
    )
    logger.info(
        f"Done: {stats.imported} of {stats.launches} runs "
        f"{'would be ' if args.dry_run else ''}imported, {stats.assets} assets "
        f"({stats.with_report} with a logged report). Skipped: "
        f"{stats.skipped_existing} already imported, {stats.skipped_empty} "
        f"without acceptance results, {stats.performance_tests_skipped} "
        f"performance tests. {stats.unknown_env} runs have no environment."
    )
    for launch_id, error in stats.failed:
        logger.error(f"Failed: launch {launch_id}: {error}")
    return 1 if stats.failed else 0


if __name__ == "__main__":
    sys.exit(cli())
