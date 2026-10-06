"""Read queries behind the dashboard's views and the read API."""

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from radiator.models import AgentResult, AssetResult, PerformanceResult, Run

STATUSES = ["PASSED", "FAILED", "NO_RESULTS", "ERROR", "SKIPPED"]
# Statuses that mean the agent answered and got it wrong, as opposed to the
# test not happening (SKIPPED).
NOT_PASSING = {"FAILED", "NO_RESULTS", "ERROR"}

# Fixed display order, so the ARS leads and an agent's column (and color) never
# moves between runs. Agents not listed sort after these, alphabetically.
AGENT_ORDER = [
    "ars",
    "aragorn",
    "arax",
    "biothings-explorer",
    "improving-agent",
    "unsecret-agent",
    "cqs",
    "shepherd-aragorn",
    "shepherd-arax",
    "shepherd-bte",
]


def agent_sort_key(agent: str):
    try:
        return (0, AGENT_ORDER.index(agent), agent)
    except ValueError:
        return (1, 0, agent)


def pass_rate(counts: dict) -> Optional[float]:
    """Passed over everything that actually ran (skips excluded)."""
    ran = sum(counts.get(status, 0) for status in STATUSES if status != "SKIPPED")
    if ran == 0:
        return None
    return counts.get("PASSED", 0) / ran


# --- runs ------------------------------------------------------------------


def list_runs(
    session: Session,
    suite: Optional[str] = None,
    env: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Run]:
    stmt = select(Run).order_by(Run.started_at.desc()).limit(limit).offset(offset)
    if suite:
        stmt = stmt.where(Run.suite == suite)
    if env:
        stmt = stmt.where(Run.env == env)
    return list(session.scalars(stmt))


def count_runs(session: Session, suite=None, env=None) -> int:
    stmt = select(func.count()).select_from(Run)
    if suite:
        stmt = stmt.where(Run.suite == suite)
    if env:
        stmt = stmt.where(Run.env == env)
    return session.scalar(stmt)


def filter_options(session: Session) -> dict:
    return {
        "suites": list(
            session.scalars(select(Run.suite).distinct().order_by(Run.suite))
        ),
        "envs": [
            env
            for env in session.scalars(select(Run.env).distinct().order_by(Run.env))
            if env
        ],
    }


def status_counts(session: Session, run_ids: list[uuid.UUID]) -> dict:
    """Overall (asset-level) status counts per run, from the stored results.

    Computed rather than read from ``Run.counts``: imported and unfinished runs
    have no counts, and these always agree with what the run page shows.
    """
    counts: dict = {run_id: {} for run_id in run_ids}
    if not run_ids:
        return counts
    rows = session.execute(
        select(AssetResult.run_id, AssetResult.status, func.count())
        .where(AssetResult.run_id.in_(run_ids))
        .group_by(AssetResult.run_id, AssetResult.status)
    )
    for run_id, status, n in rows:
        counts[run_id][status] = n
    return counts


def performance_counts(session: Session, run_ids: list[uuid.UUID]) -> dict:
    """Performance verdict counts per run, for runs with performance results."""
    counts: dict = defaultdict(dict)
    if not run_ids:
        return counts
    rows = session.execute(
        select(PerformanceResult.run_id, PerformanceResult.status, func.count())
        .where(PerformanceResult.run_id.in_(run_ids))
        .group_by(PerformanceResult.run_id, PerformanceResult.status)
    )
    for run_id, status, n in rows:
        counts[run_id][status] = n
    return counts


def get_run(session: Session, run_id: uuid.UUID) -> Optional[Run]:
    return session.scalar(
        select(Run)
        .where(Run.id == run_id)
        .options(
            selectinload(Run.assets).selectinload(AssetResult.agents),
            selectinload(Run.performance),
        )
    )


def previous_run(session: Session, run: Run) -> Optional[Run]:
    """The run before this one in the same series.

    A series is the same suite, env, target override, and query type: a run
    against a locally running ARA, or of only the MVP1 queries, isn't
    comparable with a full run against the deployed services.
    """
    return session.scalar(
        select(Run)
        .where(
            *_same_series(run),
            Run.started_at < run.started_at,
            # a crashed or still-uploading run isn't a fair baseline
            Run.ended_at.is_not(None),
        )
        .order_by(Run.started_at.desc())
        .limit(1)
    )


def _same_series(run: Run) -> list:
    return [
        Run.suite == run.suite,
        Run.env.is_not_distinct_from(run.env),
        Run.target.is_not_distinct_from(run.target),
        Run.query_type.is_not_distinct_from(run.query_type),
    ]


def series_history(
    session: Session, run: Run, limit: int = 30
) -> list[tuple[Run, dict[str, dict[str, int]]]]:
    """The finished runs of this run's series up to and including it, oldest
    first, each with its per-agent status counts."""
    earlier = list(
        session.scalars(
            select(Run)
            .where(
                *_same_series(run),
                Run.started_at < run.started_at,
                Run.ended_at.is_not(None),
            )
            .order_by(Run.started_at.desc())
            .limit(max(limit - 1, 0))
        )
    )
    runs = list(reversed(earlier)) + [run]
    rows = session.execute(
        select(AssetResult.run_id, AgentResult.agent, AgentResult.status, func.count())
        .join(AgentResult, AgentResult.asset_result_id == AssetResult.id)
        .where(AssetResult.run_id.in_([r.id for r in runs]))
        .group_by(AssetResult.run_id, AgentResult.agent, AgentResult.status)
    )
    counts: dict = {r.id: defaultdict(dict) for r in runs}
    for run_id, agent, status, n in rows:
        counts[run_id][agent][status] = n
    return [(r, dict(counts[r.id])) for r in runs]


def _performance_summary(session: Session, run: Run) -> list[dict]:
    """Each service's top concurrency in this run against its previous run."""
    run = get_run(session, run.id)
    out = []
    for perf, history in performance_series(session, run, limit=2):
        previous = history[-2][1] if len(history) > 1 else None
        out.append(
            {
                "host": perf.host,
                "helmsdeep_target": perf.helmsdeep_target,
                "profile": perf.profile,
                "status": perf.status,
                "error": perf.error,
                "max_sustainable_concurrency": perf.max_sustainable_concurrency,
                "knee_unsupported": perf.knee_unsupported,
                "checkpoints_passed": perf.checkpoints_passed,
                "previous_max_sustainable_concurrency": (
                    previous.max_sustainable_concurrency if previous else None
                ),
            }
        )
    return out


def run_summary(session: Session, run: Run) -> dict:
    """The headline of a run against the previous one in its series, for
    notifications (eg the harness's Slack report)."""
    overall = status_counts(session, [run.id])[run.id]
    previous = previous_run(session, run)
    summary = {
        "run_id": str(run.id),
        "performance": _performance_summary(session, run),
        "pass_rate": pass_rate(overall),
        "counts": overall,
        "previous_run_id": None,
        "previous_pass_rate": None,
        "regressions": None,
        "fixed": None,
    }
    if previous is not None:
        diff = diff_runs(get_run(session, previous.id), get_run(session, run.id))
        summary.update(
            previous_run_id=str(previous.id),
            previous_pass_rate=pass_rate(
                status_counts(session, [previous.id])[previous.id]
            ),
            regressions=diff.count("regression"),
            fixed=diff.count("fixed"),
        )
    return summary


def run_agents(run: Run) -> list[str]:
    agents = {agent.agent for asset in run.assets for agent in asset.agents}
    return sorted(agents, key=agent_sort_key)


def agent_counts(run: Run) -> dict[str, dict[str, int]]:
    counts: dict = defaultdict(lambda: defaultdict(int))
    for asset in run.assets:
        for agent in asset.agents:
            counts[agent.agent][agent.status] += 1
    return counts


# --- diff ------------------------------------------------------------------

DIFF_KINDS = [
    ("regression", "Regressions", "Passed last run, not passing now"),
    ("fixed", "Fixed", "Not passing last run, passing now"),
    ("changed", "Other status changes", "Changed between non-passing statuses"),
    ("skipped", "Skips", "Started or stopped being skipped"),
    ("rank", "Rank moves", "Same status, expected answer moved 5 or more places"),
    ("added", "New assets", "Not in the previous run"),
    ("removed", "Dropped assets", "In the previous run, not in this one"),
]


@dataclass
class Change:
    kind: str
    asset: AssetResult
    agent: Optional[str] = None
    before: Optional[AgentResult] = None
    after: Optional[AgentResult] = None

    @property
    def rank_delta(self) -> Optional[int]:
        """Positive when the expected answer moved up (a smaller rank)."""
        if self.before is None or self.after is None:
            return None
        if self.before.rank is None or self.after.rank is None:
            return None
        return self.before.rank - self.after.rank


@dataclass
class Diff:
    base: Optional[Run]
    changes: dict[str, list[Change]] = field(default_factory=dict)

    def count(self, kind: str) -> int:
        return len(self.changes.get(kind, []))


# Smaller rank moves are mostly scoring noise between runs.
RANK_MOVE_THRESHOLD = 5


def _classify(before: AgentResult, after: AgentResult) -> Optional[str]:
    if before.status != after.status:
        if "SKIPPED" in (before.status, after.status):
            return "skipped"
        if before.status == "PASSED":
            return "regression"
        if after.status == "PASSED":
            return "fixed"
        return "changed"
    if (
        before.rank is not None
        and after.rank is not None
        and abs(before.rank - after.rank) >= RANK_MOVE_THRESHOLD
    ):
        return "rank"
    return None


def diff_runs(base: Optional[Run], run: Run) -> Diff:
    diff = Diff(base=base, changes={kind: [] for kind, _, _ in DIFF_KINDS})
    if base is None:
        return diff
    before_assets = {(a.test_case_id, a.asset_id): a for a in base.assets}
    after_assets = {(a.test_case_id, a.asset_id): a for a in run.assets}

    for key, asset in after_assets.items():
        previous = before_assets.get(key)
        if previous is None:
            diff.changes["added"].append(Change("added", asset))
            continue
        before_agents = {a.agent: a for a in previous.agents}
        for after in asset.agents:
            before = before_agents.get(after.agent)
            if before is None:
                continue
            kind = _classify(before, after)
            if kind:
                diff.changes[kind].append(
                    Change(kind, asset, after.agent, before, after)
                )
    for key, asset in before_assets.items():
        if key not in after_assets:
            diff.changes["removed"].append(Change("removed", asset))

    for changes in diff.changes.values():
        changes.sort(
            key=lambda c: (
                agent_sort_key(c.agent or ""),
                -abs(c.rank_delta or 0),
                c.asset.name or "",
            )
        )
    return diff


# --- asset history -----------------------------------------------------------

# How the asset page names "runs with no environment" (eg imported ones).
NO_ENV = "-"


@dataclass
class AssetHistory:
    test_case_id: str
    asset_id: str
    latest: Optional[AssetResult]
    # oldest first
    points: list[tuple[Run, AssetResult]]
    agents: list[str]

    def agent_result(self, asset: AssetResult, agent: str) -> Optional[AgentResult]:
        for result in asset.agents:
            if result.agent == agent:
                return result
        return None


def asset_history(
    session: Session,
    test_case_id: str,
    asset_id: str,
    env: Optional[str] = None,
    limit: int = 60,
) -> AssetHistory:
    stmt = (
        select(Run, AssetResult)
        .join(AssetResult, AssetResult.run_id == Run.id)
        .where(
            AssetResult.test_case_id == test_case_id,
            AssetResult.asset_id == asset_id,
        )
        .options(selectinload(AssetResult.agents))
        .order_by(Run.started_at.desc())
        .limit(limit)
    )
    if env == NO_ENV:
        stmt = stmt.where(Run.env.is_(None))
    elif env:
        stmt = stmt.where(Run.env == env)
    points = list(reversed(session.execute(stmt).all()))
    agents = sorted(
        {agent.agent for _, asset in points for agent in asset.agents},
        key=agent_sort_key,
    )
    return AssetHistory(
        test_case_id=test_case_id,
        asset_id=asset_id,
        latest=points[-1][1] if points else None,
        points=points,
        agents=agents,
    )


def asset_envs(session: Session, test_case_id: str, asset_id: str) -> list[str]:
    """The environments an asset has history in, most history first.

    Runs with no environment are listed as ``NO_ENV``.
    """
    rows = session.execute(
        select(Run.env, func.count())
        .join(AssetResult, AssetResult.run_id == Run.id)
        .where(
            AssetResult.test_case_id == test_case_id,
            AssetResult.asset_id == asset_id,
        )
        .group_by(Run.env)
        .order_by(func.count().desc(), Run.env)
    )
    return [env or NO_ENV for env, _ in rows]


# --- trends ----------------------------------------------------------------


@dataclass
class TrendPoint:
    run: Run
    # agent -> expected_output -> status -> count
    counts: dict


def agent_trends(
    session: Session, suite: str, env: Optional[str], days: int = 90
) -> list[TrendPoint]:
    """Per-run, per-agent status counts by expected output, oldest first.

    Only full runs of the suite: target overrides and single-query-type runs
    measure something else and would make the line jump.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    run_filter = [
        Run.suite == suite,
        Run.started_at >= since,
        Run.target.is_(None),
        Run.query_type.is_(None),
        Run.ended_at.is_not(None),
    ]
    if env:
        run_filter.append(Run.env == env)
    runs = list(
        session.scalars(select(Run).where(*run_filter).order_by(Run.started_at))
    )
    if not runs:
        return []
    rows = session.execute(
        select(
            AssetResult.run_id,
            AgentResult.agent,
            AssetResult.expected_output,
            AgentResult.status,
            func.count(),
        )
        .join(AgentResult, AgentResult.asset_result_id == AssetResult.id)
        .where(AssetResult.run_id.in_([run.id for run in runs]))
        .group_by(
            AssetResult.run_id,
            AgentResult.agent,
            AssetResult.expected_output,
            AgentResult.status,
        )
    )
    by_run: dict = {
        run.id: defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
        for run in runs
    }
    for run_id, agent, expected, status, n in rows:
        by_run[run_id][agent][expected or "Unspecified"][status] += n
    return [TrendPoint(run=run, counts=by_run[run.id]) for run in runs]


# --- performance -------------------------------------------------------------


def performance_history(
    session: Session, days: int = 180
) -> dict[str, list[tuple[Run, PerformanceResult]]]:
    """Performance results per host (and HelmsDeep target), oldest first."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = session.execute(
        select(Run, PerformanceResult)
        .join(PerformanceResult, PerformanceResult.run_id == Run.id)
        .where(Run.started_at >= since)
        .order_by(Run.started_at)
    )
    series: dict = defaultdict(list)
    for run, perf in rows:
        series[perf_series_name(perf, run.env)].append((run, perf))
    return dict(sorted(series.items()))


def perf_series_name(perf: PerformanceResult, env: Optional[str] = None) -> str:
    """A service's performance series, by name.

    A series is one host under one HelmsDeep run type and profile, in one
    environment: a mixed-profile run and a default one load the service
    differently, so their numbers aren't comparable.
    """
    detail = ", ".join(
        part for part in (perf.helmsdeep_target, perf.profile or "default", env) if part
    )
    return f"{perf.host} ({detail})"


def performance_series(
    session: Session, run: Run, limit: int = 30
) -> list[tuple[PerformanceResult, list[tuple[Run, PerformanceResult]]]]:
    """For each performance result in this run, its series' history up to and
    including it, oldest first, from finished runs."""
    out = []
    for perf in sorted(run.performance, key=lambda p: p.host):
        earlier = session.execute(
            select(Run, PerformanceResult)
            .join(PerformanceResult, PerformanceResult.run_id == Run.id)
            .where(
                PerformanceResult.host == perf.host,
                PerformanceResult.helmsdeep_target.is_not_distinct_from(
                    perf.helmsdeep_target
                ),
                PerformanceResult.profile.is_not_distinct_from(perf.profile),
                Run.env.is_not_distinct_from(run.env),
                Run.started_at < run.started_at,
                Run.ended_at.is_not(None),
            )
            .order_by(Run.started_at.desc())
            .limit(max(limit - 1, 0))
        ).all()
        out.append((perf, [tuple(row) for row in reversed(earlier)] + [(run, perf)]))
    return out


# --- JSON for the read API ---------------------------------------------------


def run_json(run: Run, with_results: bool = False) -> dict:
    data = {
        "run_id": str(run.id),
        "suite": run.suite,
        "env": run.env,
        "target": run.target,
        "query_type": run.query_type,
        "harness_version": run.harness_version,
        "origin": run.origin,
        "started_at": run.started_at.isoformat(),
        "ended_at": run.ended_at.isoformat() if run.ended_at else None,
        "counts": run.counts,
    }
    if with_results:
        data["results"] = [
            {
                "test_case_id": asset.test_case_id,
                "asset_id": asset.asset_id,
                "name": asset.name,
                "kind": asset.kind,
                "expected_output": asset.expected_output,
                "input_curie": asset.input_curie,
                "output_curie": asset.output_curie,
                "status": asset.status,
                "agents": [
                    {
                        "agent": agent.agent,
                        "status": agent.status,
                        "found": agent.found,
                        "rank": agent.rank,
                        "score": agent.score,
                        "n_results": agent.n_results,
                        "http_status": agent.http_status,
                        "response_time_s": agent.response_time_s,
                        "pk": agent.pk,
                    }
                    for agent in asset.agents
                ],
            }
            for asset in run.assets
        ]
        data["performance"] = [
            {
                "host": perf.host,
                "helmsdeep_target": perf.helmsdeep_target,
                "profile": perf.profile,
                "status": perf.status,
                "max_sustainable_concurrency": perf.max_sustainable_concurrency,
                "checkpoints_passed": perf.checkpoints_passed,
                "error": perf.error,
            }
            for perf in run.performance
        ]
    return data
