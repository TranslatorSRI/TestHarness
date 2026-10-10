"""Run cycles, the board, flags, and the weekly digest.

A run cycle is acceptance, then pathfinder, then performance, run back to back
in one environment by ``test-harness-cycle``; its runs share a ``cycle_id``.
The *board* is one row per test in the cycle, each against the previous run of
the same test in that environment: the headline number, its change, and a
verdict (regressed, improved, mixed, steady). *Flags* point out oddities a pass
rate hides: an agent that stopped returning results, started erroring, got much
slower, or ranked the expected answer much lower while still passing.

The *digest* is the same rows for every environment: each one's latest runs of
each test over the past week, whether or not a cycle ran them.
"""

import statistics
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from radiator import queries
from radiator.grid import ENV_ORDER
from radiator.models import AssetResult, PerformanceResult, Run

ACCEPTANCE, PATHFINDER, PERFORMANCE = "acceptance", "pathfinder", "performance"
KINDS = [ACCEPTANCE, PATHFINDER, PERFORMANCE]
KIND_LABELS = {
    ACCEPTANCE: "Acceptance",
    PATHFINDER: "Pathfinder",
    PERFORMANCE: "Performance",
}

REGRESSED, MIXED, IMPROVED, STEADY, FIRST, NO_DATA = (
    "regressed",
    "mixed",
    "improved",
    "steady",
    "first",
    "nodata",
)
VERDICT_LABELS = {
    REGRESSED: "Regressed",
    MIXED: "Mixed",
    IMPROVED: "Improved",
    STEADY: "Steady",
    FIRST: "First run",
    NO_DATA: "No result",
}
# Worst first: the order a row list or a digest cell sorts its verdicts in.
VERDICT_ORDER = [NO_DATA, REGRESSED, MIXED, IMPROVED, STEADY, FIRST]

# A concurrency change smaller than this is noise between runs.
PERF_STEADY_BAND = 0.10

# Flag thresholds. Kept together so they're easy to tune.
FLAG_MIN_ASSETS = 3  # no results / errors: on at least this many assets
SLOWER_RATIO = 2.0  # median response time at least doubled...
SLOWER_MIN_S = 5.0  # ...and went up by at least this many seconds
RANK_DROP = 30  # still passing, but the expected answer fell this many places
CONCURRENCY_DROP = 0.25  # max sustainable concurrency fell by this fraction

# How many assets a flag or a change list names before "and N more".
NAMED_ASSETS = 5

HISTORY_RUNS = 8


@dataclass
class AssetRef:
    test_case_id: str
    asset_id: str
    name: Optional[str]

    @classmethod
    def of(cls, asset: AssetResult) -> "AssetRef":
        return cls(asset.test_case_id, asset.asset_id, asset.name)

    @property
    def label(self) -> str:
        return self.name or f"{self.test_case_id}/{self.asset_id}"


@dataclass
class Flag:
    kind: str
    who: str  # an agent, or a performance target
    text: str
    run_id: uuid.UUID
    assets: list[AssetRef] = field(default_factory=list)


@dataclass
class AgentChanges:
    agent: str
    regressions: list[AssetRef] = field(default_factory=list)
    fixed: list[AssetRef] = field(default_factory=list)


@dataclass
class Row:
    run: Run
    kind: str
    verdict: str
    value: Optional[float]  # pass rate (0..1), or max sustainable concurrency
    previous: Optional[float]
    previous_run: Optional[Run]
    history: list[Optional[float]]  # oldest first, ending with this run
    regressions: Optional[int] = None
    fixed: Optional[int] = None
    counts: dict = field(default_factory=dict)
    perf: Optional[PerformanceResult] = None
    flags: list[Flag] = field(default_factory=list)
    changes: list[AgentChanges] = field(default_factory=list)

    @property
    def label(self) -> str:
        if self.perf is not None:
            return f"{KIND_LABELS[self.kind]} · {_perf_name(self.perf)}"
        return KIND_LABELS[self.kind]

    @property
    def value_text(self) -> str:
        if self.value is None:
            if self.perf is not None:
                return "no result"
            return "all skipped"
        if self.perf is not None:
            return f"{self.value:.1f}"
        return f"{self.value * 100:.1f}%"

    @property
    def unit(self) -> str:
        return "max concurrency" if self.perf is not None else "pass rate"

    @property
    def change_text(self) -> str:
        if self.previous_run is None:
            return "first run"
        if self.value is None or self.previous is None:
            return "–"
        delta = self.value - self.previous
        if self.perf is not None:
            if abs(delta) < 0.05:
                return "no change"
            pct = f" ({delta / self.previous:+.0%})" if self.previous else ""
            return f"{_arrow(delta)}{abs(delta):.1f}{pct}"
        if abs(delta * 100) < 0.1:
            return "no change"
        return f"{_arrow(delta)}{abs(delta) * 100:.1f} pts"

    @property
    def detail_text(self) -> str:
        if self.perf is not None:
            if self.perf.error:
                return f"run failed: {self.perf.error}"
            if self.perf.checkpoints_passed is True:
                return "checkpoints passed"
            if self.perf.checkpoints_passed is False:
                return "checkpoints missed"
            return "no checkpoints"
        if self.regressions is None:
            passed = self.counts.get("PASSED", 0)
            total = sum(self.counts.values())
            return f"{passed} of {total} passed"
        return f"{_plural(self.regressions, 'regression')} · {self.fixed} fixed"


def _arrow(delta: float) -> str:
    return "▲" if delta > 0 else "▼"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _perf_name(perf: PerformanceResult) -> str:
    return perf.helmsdeep_target or perf.host


# --- which test a run is ---------------------------------------------------------


def run_kinds(session: Session, run_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    """acceptance, pathfinder or performance, per run, from what it recorded.

    A run with performance results is a performance run; one whose assets are
    all pathfinder assets is a pathfinder run; anything else is acceptance.
    """
    kinds: dict = {run_id: ACCEPTANCE for run_id in run_ids}
    if not run_ids:
        return kinds
    asset_kinds: dict = defaultdict(set)
    for run_id, kind in session.execute(
        select(AssetResult.run_id, AssetResult.kind)
        .where(AssetResult.run_id.in_(run_ids))
        .distinct()
    ):
        asset_kinds[run_id].add(kind)
    for run_id, kinds_seen in asset_kinds.items():
        if kinds_seen == {PATHFINDER}:
            kinds[run_id] = PATHFINDER
    for run_id in session.scalars(
        select(PerformanceResult.run_id)
        .where(PerformanceResult.run_id.in_(run_ids))
        .distinct()
    ):
        kinds[run_id] = PERFORMANCE
    return kinds


# --- flags ------------------------------------------------------------------------


def _had_results(agent) -> bool:
    if agent.n_results is not None:
        return agent.n_results > 0
    return agent.status in ("PASSED", "FAILED")


def _no_results(agent) -> bool:
    return agent.status == "NO_RESULTS" or agent.n_results == 0


def _errored(agent) -> bool:
    return agent.status == "ERROR" or (agent.http_status or 0) >= 400


def _named(assets: list[AssetRef]) -> str:
    names = ", ".join(a.label for a in assets[:NAMED_ASSETS])
    more = len(assets) - NAMED_ASSETS
    return names + (f" and {more} more" if more > 0 else "")


def result_flags(base: Optional[Run], run: Run) -> list[Flag]:
    """Oddities in an acceptance or pathfinder run against the previous run."""
    if base is None:
        return []
    before_assets = {(a.test_case_id, a.asset_id): a for a in base.assets}
    lost: dict = defaultdict(list)
    errors: dict = defaultdict(list)
    dropped: dict = defaultdict(list)
    times_before: dict = defaultdict(list)
    times_after: dict = defaultdict(list)
    for asset in run.assets:
        previous = before_assets.get((asset.test_case_id, asset.asset_id))
        if previous is None:
            continue
        before_agents = {a.agent: a for a in previous.agents}
        for after in asset.agents:
            before = before_agents.get(after.agent)
            if before is None:
                continue
            ref = AssetRef.of(asset)
            if _had_results(before) and _no_results(after):
                lost[after.agent].append(ref)
            if _errored(after) and not _errored(before):
                errors[after.agent].append(ref)
            if (
                before.status == after.status == "PASSED"
                and before.rank is not None
                and after.rank is not None
                and after.rank - before.rank >= RANK_DROP
            ):
                dropped[after.agent].append(
                    (after.rank - before.rank, ref, before, after)
                )
            if before.response_time_s is not None and after.response_time_s is not None:
                times_before[after.agent].append(before.response_time_s)
                times_after[after.agent].append(after.response_time_s)

    flags = []
    for agent, refs in lost.items():
        if len(refs) >= FLAG_MIN_ASSETS:
            flags.append(
                Flag(
                    "no_results",
                    agent,
                    f"{agent}: no results on {len(refs)} assets that had results "
                    f"last run ({_named(refs)})",
                    run.id,
                    refs,
                )
            )
    for agent, refs in errors.items():
        if len(refs) >= FLAG_MIN_ASSETS:
            flags.append(
                Flag(
                    "errors",
                    agent,
                    f"{agent}: errors on {len(refs)} assets that didn't error "
                    f"last run ({_named(refs)})",
                    run.id,
                    refs,
                )
            )
    for agent in times_after:
        before_s = statistics.median(times_before[agent])
        after_s = statistics.median(times_after[agent])
        if after_s >= SLOWER_RATIO * before_s and after_s - before_s >= SLOWER_MIN_S:
            flags.append(
                Flag(
                    "slower",
                    agent,
                    f"{agent}: median response time {before_s:.1f}s → {after_s:.1f}s",
                    run.id,
                )
            )
    for agent, drops in dropped.items():
        drops.sort(key=lambda d: -d[0])
        refs = [ref for _, ref, _, _ in drops]
        worst = ", ".join(
            f"{ref.label} ({before.rank} → {after.rank})"
            for _, ref, before, after in drops[:NAMED_ASSETS]
        )
        more = len(drops) - NAMED_ASSETS
        flags.append(
            Flag(
                "rank_drop",
                agent,
                f"{agent}: expected answer down {RANK_DROP}+ places on "
                f"{_plural(len(drops), 'asset')}, still passing: {worst}"
                + (f" and {more} more" if more > 0 else ""),
                run.id,
                refs,
            )
        )
    flags.sort(key=lambda f: (queries.agent_sort_key(f.who), f.kind))
    return flags


def performance_flags(
    run: Run, perf: PerformanceResult, previous: Optional[PerformanceResult]
) -> list[Flag]:
    """Oddities in a performance result against the previous one."""
    if previous is None:
        return []
    name = _perf_name(perf)
    was = previous.max_sustainable_concurrency
    now = perf.max_sustainable_concurrency
    flags = []
    if was is not None and now is None:
        reason = f"run failed: {perf.error}" if perf.error else "no stage met the SLO"
        flags.append(
            Flag("perf_failed", name, f"{name}: {reason} (was {was:.1f})", run.id)
        )
    elif was and now is not None and now <= (1 - CONCURRENCY_DROP) * was:
        flags.append(
            Flag(
                "concurrency_drop",
                name,
                f"{name}: max sustainable concurrency {was:.1f} → {now:.1f} "
                f"({(now - was) / was:+.0%})",
                run.id,
            )
        )
    if previous.checkpoints_passed is True and perf.checkpoints_passed is False:
        flags.append(
            Flag(
                "checkpoints",
                name,
                f"{name}: missed checkpoints it passed last run",
                run.id,
            )
        )
    return flags


# --- rows ---------------------------------------------------------------------------


def _changes_by_agent(diff: queries.Diff) -> list[AgentChanges]:
    by_agent: dict = {}
    for kind in ("regression", "fixed"):
        for change in diff.changes.get(kind, []):
            entry = by_agent.setdefault(change.agent, AgentChanges(change.agent))
            target = entry.regressions if kind == "regression" else entry.fixed
            target.append(AssetRef.of(change.asset))
    return [by_agent[a] for a in sorted(by_agent, key=queries.agent_sort_key)]


def _result_verdict(row: Row) -> str:
    if row.value is None:
        return NO_DATA
    if row.previous_run is None:
        return FIRST
    # by the net change; both counts are always shown alongside
    if row.regressions > row.fixed:
        return REGRESSED
    if row.fixed > row.regressions:
        return IMPROVED
    return MIXED if row.regressions else STEADY


def _perf_verdict(row: Row, previous: Optional[PerformanceResult]) -> str:
    if row.value is None:
        return NO_DATA
    if previous is None:
        return FIRST
    if previous.checkpoints_passed is True and row.perf.checkpoints_passed is False:
        return REGRESSED
    if not row.previous:  # the last run measured nothing
        return IMPROVED if row.value else STEADY
    change = (row.value - row.previous) / row.previous
    if change <= -PERF_STEADY_BAND:
        return REGRESSED
    if change >= PERF_STEADY_BAND:
        return IMPROVED
    return STEADY


def _result_row(session: Session, run: Run, kind: str) -> Row:
    run = queries.get_run(session, run.id)
    previous = queries.previous_run(session, run)
    history_runs = [r for r, _ in queries.series_history(session, run, HISTORY_RUNS)]
    counts = queries.status_counts(session, [r.id for r in history_runs])
    history = [queries.pass_rate(counts[r.id]) for r in history_runs]
    row = Row(
        run=run,
        kind=kind,
        verdict=FIRST,
        value=history[-1],
        previous=None,
        previous_run=previous,
        history=history,
        counts=counts[run.id],
    )
    if previous is not None:
        base = queries.get_run(session, previous.id)
        diff = queries.diff_runs(base, run)
        row.previous = queries.pass_rate(
            queries.status_counts(session, [base.id])[base.id]
        )
        row.regressions = diff.count("regression")
        row.fixed = diff.count("fixed")
        row.changes = _changes_by_agent(diff)
        row.flags = result_flags(base, run)
    row.verdict = _result_verdict(row)
    return row


def _perf_rows(session: Session, run: Run) -> list[Row]:
    run = queries.get_run(session, run.id)
    rows = []
    series = queries.performance_series(session, run, HISTORY_RUNS)
    for perf, history in sorted(
        series, key=lambda s: queries.agent_sort_key(_perf_name(s[0]))
    ):
        previous_run, previous = history[-2] if len(history) > 1 else (None, None)
        row = Row(
            run=run,
            kind=PERFORMANCE,
            verdict=FIRST,
            value=perf.max_sustainable_concurrency,
            previous=previous.max_sustainable_concurrency if previous else None,
            previous_run=previous_run,
            history=[p.max_sustainable_concurrency for _, p in history],
            perf=perf,
        )
        row.flags = performance_flags(run, perf, previous)
        row.verdict = _perf_verdict(row, previous)
        rows.append(row)
    return rows


def rows_for(session: Session, runs: Iterable[Run]) -> list[Row]:
    """Board rows for these runs: acceptance, then pathfinder, then
    performance, each kind in the order its runs started."""
    runs = sorted(runs, key=lambda r: r.started_at)
    kinds = run_kinds(session, [r.id for r in runs])
    rows = []
    for kind in KINDS:
        of_kind = []
        for run in runs:
            if kinds[run.id] != kind:
                continue
            if kind == PERFORMANCE:
                of_kind.extend(_perf_rows(session, run))
            else:
                of_kind.append(_result_row(session, run, kind))
        if kind == PERFORMANCE:
            # the ARS first, then the ARAs, whatever order they ran in
            of_kind.sort(key=lambda row: queries.agent_sort_key(_perf_name(row.perf)))
        rows.extend(of_kind)
    return rows


# --- cycles -------------------------------------------------------------------------


@dataclass
class Cycle:
    cycle_id: uuid.UUID
    runs: list[Run]
    rows: list[Row]

    @property
    def env(self) -> Optional[str]:
        envs = {r.env for r in self.runs}
        return envs.pop() if len(envs) == 1 else None

    @property
    def started_at(self) -> datetime:
        return min(r.started_at for r in self.runs)

    @property
    def ended_at(self) -> Optional[datetime]:
        ends = [r.ended_at for r in self.runs]
        return None if None in ends else max(ends)

    @property
    def flags(self) -> list[Flag]:
        return [flag for row in self.rows for flag in row.flags]


def get_cycle(session: Session, cycle_id: uuid.UUID) -> Optional[Cycle]:
    runs = list(
        session.scalars(
            select(Run).where(Run.cycle_id == cycle_id).order_by(Run.started_at)
        )
    )
    if not runs:
        return None
    return Cycle(cycle_id, runs, rows_for(session, runs))


def recent_cycles(
    session: Session, limit: int = 20
) -> list[tuple[uuid.UUID, Optional[str], datetime]]:
    """(cycle id, env, start) of the latest cycles, newest first."""
    from sqlalchemy import func

    rows = session.execute(
        select(Run.cycle_id, func.min(Run.env), func.min(Run.started_at))
        .where(Run.cycle_id.is_not(None))
        .group_by(Run.cycle_id)
        .order_by(func.min(Run.started_at).desc())
        .limit(limit)
    )
    return [tuple(row) for row in rows]


# --- the digest ---------------------------------------------------------------------


@dataclass
class EnvDigest:
    env: Optional[str]
    rows: list[Row]
    cycle_ids: list[uuid.UUID]

    def cell(self, kind: str) -> list[Row]:
        return [row for row in self.rows if row.kind == kind]

    @property
    def flags(self) -> list[Flag]:
        return [flag for row in self.rows for flag in row.flags]


@dataclass
class Digest:
    since: datetime
    until: datetime
    envs: list[EnvDigest]


def _env_key(env: Optional[str]):
    if env is None:
        return (2, 0, "")
    if env in ENV_ORDER:
        return (0, ENV_ORDER.index(env), env)
    return (1, 0, env)


def digest(session: Session, until: Optional[datetime] = None, days: int = 7) -> Digest:
    """Every environment's latest finished run of each test over ``days``.

    Full runs only: a run of one query type is a slice, and an acceptance run
    against an overridden target isn't the environment's. Performance runs
    always name their target, so they're kept, one row per service.
    """
    until = until or datetime.now(timezone.utc)
    since = until - timedelta(days=days)
    runs = list(
        session.scalars(
            select(Run)
            .where(
                Run.started_at >= since,
                Run.started_at <= until,
                Run.ended_at.is_not(None),
                Run.query_type.is_(None),
            )
            .order_by(Run.started_at.desc())
        )
    )
    kinds = run_kinds(session, [r.id for r in runs])
    latest: dict = {}  # (env, kind, suite, perf key) -> run
    perf_hosts = _perf_hosts(
        session, [r.id for r in runs if kinds[r.id] == PERFORMANCE]
    )
    for run in runs:  # newest first, so the first one seen is the latest
        kind = kinds[run.id]
        if kind == PERFORMANCE:
            for host in perf_hosts.get(run.id, []):
                latest.setdefault((run.env, kind, run.suite, host), run)
        elif run.target is None:
            latest.setdefault((run.env, kind, run.suite, None), run)

    by_env: dict = defaultdict(dict)
    for (env, kind, suite, host), run in latest.items():
        by_env[env].setdefault(run.id, (run, set()))[1].add(host)
    envs = []
    for env in sorted(by_env, key=_env_key):
        picked = by_env[env]
        rows = []
        for row in rows_for(session, [run for run, _ in picked.values()]):
            # a performance run may have measured other hosts too; keep only
            # the ones this run is the latest for
            if row.perf is not None and row.perf.host not in picked[row.run.id][1]:
                continue
            rows.append(row)
        cycle_ids = sorted(
            {run.cycle_id for run, _ in picked.values() if run.cycle_id},
            key=str,
        )
        envs.append(EnvDigest(env, rows, cycle_ids))
    return Digest(since, until, envs)


def _perf_hosts(session: Session, run_ids: list[uuid.UUID]) -> dict:
    hosts: dict = defaultdict(list)
    if not run_ids:
        return hosts
    for run_id, host in session.execute(
        select(PerformanceResult.run_id, PerformanceResult.host).where(
            PerformanceResult.run_id.in_(run_ids)
        )
    ):
        hosts[run_id].append(host)
    return hosts


# --- JSON ---------------------------------------------------------------------------


def _asset_json(ref: AssetRef) -> dict:
    return {
        "test_case_id": ref.test_case_id,
        "asset_id": ref.asset_id,
        "name": ref.name,
    }


def flag_json(flag: Flag) -> dict:
    return {
        "kind": flag.kind,
        "who": flag.who,
        "text": flag.text,
        "run_id": str(flag.run_id),
        "assets": [_asset_json(a) for a in flag.assets],
    }


def row_json(row: Row) -> dict:
    return {
        "run_id": str(row.run.id),
        "number": row.run.number,
        "suite": row.run.suite,
        "env": row.run.env,
        "kind": row.kind,
        "label": row.label,
        "verdict": row.verdict,
        "verdict_label": VERDICT_LABELS[row.verdict],
        "value": row.value,
        "previous": row.previous,
        "previous_run_id": str(row.previous_run.id) if row.previous_run else None,
        "value_text": row.value_text,
        "unit": row.unit,
        "change_text": row.change_text,
        "detail_text": row.detail_text,
        "regressions": row.regressions,
        "fixed": row.fixed,
        "started_at": row.run.started_at.isoformat(),
        "host": row.perf.host if row.perf is not None else None,
        "flags": [flag_json(f) for f in row.flags],
        "changes": [
            {
                "agent": c.agent,
                "regressions": [_asset_json(a) for a in c.regressions],
                "fixed": [_asset_json(a) for a in c.fixed],
            }
            for c in row.changes
        ],
    }


def cycle_json(cycle: Cycle) -> dict:
    return {
        "cycle_id": str(cycle.cycle_id),
        "env": cycle.env,
        "started_at": cycle.started_at.isoformat(),
        "ended_at": cycle.ended_at.isoformat() if cycle.ended_at else None,
        "rows": [row_json(row) for row in cycle.rows],
    }


def digest_json(d: Digest) -> dict:
    return {
        "since": d.since.isoformat(),
        "until": d.until.isoformat(),
        "envs": [
            {
                "env": e.env,
                "cycle_ids": [str(c) for c in e.cycle_ids],
                "rows": [row_json(row) for row in e.rows],
            }
            for e in d.envs
        ],
    }
