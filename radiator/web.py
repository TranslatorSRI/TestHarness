"""The dashboard's pages. Everything here requires the shared login."""

import uuid
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from markupsafe import Markup, escape
from sqlalchemy.orm import Session

from radiator import charts, queries
from radiator.api import get_session
from radiator.auth import is_logged_in


class LoginRequired(Exception):
    """Raised for a page request without a session; redirects to /login."""


def require_login(request: Request) -> None:
    if not is_logged_in(request):
        raise LoginRequired()


router = APIRouter(dependencies=[Depends(require_login)])

STATUS_LABELS = {
    "PASSED": ("✓", "Passed"),
    "FAILED": ("✕", "Failed"),
    "NO_RESULTS": ("∅", "No results"),
    "ERROR": ("!", "Error"),
    "SKIPPED": ("–", "Skipped"),
}

# Where a pk is opened: the ARAX UI on ci, for every environment, as the
# harness has always linked them.
PK_VIEWER = "https://arax.ci.transltr.io/?r={pk}"

TREND_WINDOWS = [30, 90, 180, 365]


# --- template helpers ----------------------------------------------------------


def status_badge(status: str) -> Markup:
    icon, label = STATUS_LABELS.get(status, ("?", status))
    return Markup(
        f'<span class="status st-{escape(status)}"><span class="icon" aria-hidden="true">'
        f"{icon}</span>{escape(label)}</span>"
    )


def status_bar(counts: dict) -> Markup:
    total = sum(counts.values())
    if not total:
        return Markup('<span class="muted">no results</span>')
    segments = []
    for status in queries.STATUSES:
        n = counts.get(status, 0)
        if not n:
            continue
        label = STATUS_LABELS[status][1]
        tip = f"{label}\n{n} of {total} ({n / total:.0%})"
        segments.append(
            f'<span class="seg-{status}" style="flex:{n}" data-tip="{escape(tip)}"></span>'
        )
    return Markup(
        f'<div class="stackbar" role="img" aria-label="{escape(_counts_text(counts))}">{"".join(segments)}</div>'
    )


def _counts_text(counts: dict) -> str:
    return ", ".join(
        f"{counts[s]} {STATUS_LABELS[s][1].lower()}"
        for s in queries.STATUSES
        if counts.get(s)
    )


def pct(value: Optional[float], digits: int = 0) -> str:
    return "–" if value is None else f"{value * 100:.{digits}f}%"


def when(value: Optional[datetime]) -> str:
    if value is None:
        return "–"
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def ago(value: Optional[datetime]) -> str:
    if value is None:
        return ""
    seconds = (datetime.now(timezone.utc) - value).total_seconds()
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{int(seconds // size)}{unit} ago"
    return "just now"


def duration(run) -> str:
    if run.ended_at is None:
        return "–"
    minutes = int((run.ended_at - run.started_at).total_seconds() // 60)
    return f"{minutes // 60}h {minutes % 60:02d}m" if minutes >= 60 else f"{minutes}m"


def pk_url(pk: Optional[str], env: Optional[str] = None) -> Optional[str]:
    return PK_VIEWER.format(pk=pk) if pk else None


def cell_tip(asset, agent) -> str:
    lines = [
        f"{agent.agent} · {STATUS_LABELS.get(agent.status, ('', agent.status))[1]}"
    ]
    if agent.found is not None:
        lines.append(
            "expected answer found" if agent.found else "expected answer not found"
        )
    if agent.rank is not None:
        lines.append(f"rank {agent.rank}")
    if agent.score is not None:
        lines.append(f"score {agent.score:.3g}")
    if agent.n_results is not None:
        lines.append(f"{agent.n_results:,} results")
    if agent.response_time_s is not None:
        lines.append(f"{agent.response_time_s:.1f}s")
    if agent.message:
        lines.append(agent.message)
    return "\n".join(lines)


def query_string(request: Request, **changes) -> str:
    params = dict(request.query_params)
    for key, value in changes.items():
        if value is None:
            params.pop(key, None)
        else:
            params[key] = value
    return "?" + urlencode(params) if params else "?"


def register_template_helpers(templates) -> None:
    env = templates.env
    env.globals.update(
        status_badge=status_badge,
        status_bar=status_bar,
        pct=pct,
        when=when,
        ago=ago,
        duration=duration,
        pk_url=pk_url,
        cell_tip=cell_tip,
        agent_color=charts.agent_color,
        STATUS_LABELS=STATUS_LABELS,
        STATUSES=queries.STATUSES,
        query_string=query_string,
    )


def render(request: Request, template: str, **context) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(request, template, context)


# --- pages -------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
def runs_page(
    request: Request,
    suite: Optional[str] = None,
    env: Optional[str] = None,
    page: int = 1,
    session: Session = Depends(get_session),
):
    per_page = 30
    page = max(page, 1)
    runs = queries.list_runs(
        session, suite=suite, env=env, limit=per_page, offset=(page - 1) * per_page
    )
    total = queries.count_runs(session, suite=suite, env=env)
    run_ids = [run.id for run in runs]
    return render(
        request,
        "runs.html",
        nav="runs",
        runs=runs,
        counts=queries.status_counts(session, run_ids),
        perf_counts=queries.performance_counts(session, run_ids),
        pass_rate=queries.pass_rate,
        options=queries.filter_options(session),
        suite=suite,
        env=env,
        page=page,
        has_next=page * per_page < total,
        total=total,
    )


def _load_run(session: Session, run_id: uuid.UUID):
    run = queries.get_run(session, run_id)
    if run is None:
        raise HTTPException(404, "No such run")
    return run


@router.get("/runs/{run_id}", response_class=HTMLResponse)
def run_page(
    request: Request,
    run_id: uuid.UUID,
    status: Optional[str] = None,
    agent: Optional[str] = None,
    expected: Optional[str] = None,
    q: Optional[str] = None,
    session: Session = Depends(get_session),
):
    run = _load_run(session, run_id)
    previous = queries.previous_run(session, run)
    if previous is not None:
        previous = queries.get_run(session, previous.id)
    diff = queries.diff_runs(previous, run)
    agents = queries.run_agents(run)

    overall = queries.status_counts(session, [run.id])[run.id]
    previous_rate = None
    if previous is not None:
        previous_rate = queries.pass_rate(
            queries.status_counts(session, [previous.id])[previous.id]
        )

    assets = sorted(run.assets, key=lambda a: (a.test_case_id, a.name or a.asset_id))
    if status:
        if agent:
            assets = [
                a
                for a in assets
                if any(r.agent == agent and r.status == status for r in a.agents)
            ]
        else:
            assets = [a for a in assets if a.status == status]
    if expected:
        assets = [a for a in assets if a.expected_output == expected]
    if q:
        needle = q.lower()
        assets = [
            a
            for a in assets
            if needle
            in " ".join(
                filter(
                    None,
                    [a.name, a.asset_id, a.test_case_id, a.input_curie, a.output_curie],
                )
            ).lower()
        ]

    return render(
        request,
        "run.html",
        nav="runs",
        run=run,
        previous=previous,
        diff=diff,
        agents=agents,
        agent_counts=queries.agent_counts(run),
        overall=overall,
        rate=queries.pass_rate(overall),
        previous_rate=previous_rate,
        assets=assets,
        expected_outputs=sorted(
            {a.expected_output for a in run.assets if a.expected_output}
        ),
        filters=dict(status=status, agent=agent, expected=expected, q=q),
        filtered=any([status, expected, q]),
        pass_rate=queries.pass_rate,
    )


@router.get("/runs/{run_id}/diff", response_class=HTMLResponse)
def diff_page(
    request: Request,
    run_id: uuid.UUID,
    base: Optional[uuid.UUID] = None,
    session: Session = Depends(get_session),
):
    run = _load_run(session, run_id)
    if base is not None:
        base_run = _load_run(session, base)
    else:
        previous = queries.previous_run(session, run)
        base_run = queries.get_run(session, previous.id) if previous else None
    return render(
        request,
        "diff.html",
        nav="runs",
        run=run,
        base=base_run,
        diff=queries.diff_runs(base_run, run),
        kinds=queries.DIFF_KINDS,
    )


@router.get("/assets/{test_case_id}/{asset_id}", response_class=HTMLResponse)
def asset_page(
    request: Request,
    test_case_id: str,
    asset_id: str,
    env: Optional[str] = None,
    all: bool = False,
    session: Session = Depends(get_session),
):
    envs = queries.asset_envs(session, test_case_id, asset_id)
    if env is None and envs:
        # default to the env with the most history rather than mixing them
        env = "ci" if "ci" in envs else envs[0]
    history = queries.asset_history(session, test_case_id, asset_id, env=env)
    if history.latest is None:
        raise HTTPException(404, "No results for this asset")

    def series_for(attr):
        return [
            charts.Series(
                name=agent,
                color=charts.agent_color(agent),
                points=[
                    (
                        run.started_at,
                        getattr(history.agent_result(asset, agent), attr, None),
                    )
                    for run, asset in history.points
                ],
            )
            for agent in history.agents
        ]

    rank_series = [
        s for s in series_for("rank") if any(y is not None for _, y in s.points)
    ]
    count_series = [
        s for s in series_for("n_results") if any(y is not None for _, y in s.points)
    ]
    return render(
        request,
        "asset.html",
        nav="runs",
        history=history,
        envs=envs,
        env=env,
        table_runs=list(reversed(history.points))[: None if all else 10],
        show_all=all,
        rank_chart=(
            charts.line_chart(
                rank_series,
                invert=True,
                y_min=1,
                y_format=lambda v: f"{v:,.0f}",
                width=charts.WIDTH_HALF,
                label="Rank of the expected answer over time",
            )
            if rank_series
            else None
        ),
        count_chart=(
            charts.line_chart(
                count_series,
                y_min=0,
                y_format=lambda v: f"{v:,.0f}",
                width=charts.WIDTH_HALF,
                label="Number of results over time",
            )
            if count_series
            else None
        ),
    )


@router.get("/trends", response_class=HTMLResponse)
def trends_page(
    request: Request,
    suite: Optional[str] = None,
    env: Optional[str] = None,
    days: int = 90,
    session: Session = Depends(get_session),
):
    options = queries.filter_options(session)
    if suite is None and options["suites"]:
        latest = queries.list_runs(session, limit=1)
        suite = latest[0].suite if latest else options["suites"][0]
        env = env or (latest[0].env if latest else None)
    days = days if days in TREND_WINDOWS else 90
    points = queries.agent_trends(session, suite, env, days) if suite else []

    agents = sorted(
        {agent for p in points for agent in p.counts}, key=queries.agent_sort_key
    )
    expected_outputs = [
        e
        for e in ["TopAnswer", "Acceptable", "BadButForgivable", "NeverShow"]
        if any(e in p.counts.get(a, {}) for p in points for a in agents)
    ] + sorted(
        {e for p in points for a in agents for e in p.counts.get(a, {})}
        - {"TopAnswer", "Acceptable", "BadButForgivable", "NeverShow"}
    )

    def rate(point, agent, expected=None):
        by_expected = point.counts.get(agent, {})
        if expected is None:
            merged: dict = {}
            for counts in by_expected.values():
                for status, n in counts.items():
                    merged[status] = merged.get(status, 0) + n
            return queries.pass_rate(merged)
        return queries.pass_rate(by_expected.get(expected, {}))

    def chart(expected=None, height=240, width=charts.WIDTH_FULL):
        return charts.line_chart(
            [
                charts.Series(
                    agent,
                    charts.agent_color(agent),
                    [(p.run.started_at, rate(p, agent, expected)) for p in points],
                )
                for agent in agents
            ],
            y_min=0,
            y_max=1,
            y_format=lambda v: f"{v * 100:.0f}%",
            height=height,
            width=width,
            label=f"Pass rate by agent{f' for {expected}' if expected else ''}",
        )

    return render(
        request,
        "trends.html",
        nav="trends",
        options=options,
        suite=suite,
        env=env,
        days=days,
        windows=TREND_WINDOWS,
        points=points,
        agents=agents,
        overall_chart=chart() if points else None,
        multiples=(
            [
                (e, chart(e, height=200, width=charts.WIDTH_HALF))
                for e in expected_outputs
            ]
            if points
            else []
        ),
        rate=rate,
    )


@router.get("/performance", response_class=HTMLResponse)
def performance_page(
    request: Request, days: int = 180, session: Session = Depends(get_session)
):
    days = days if days in TREND_WINDOWS else 180
    history = queries.performance_history(session, days)
    panels = []
    for key, rows in history.items():
        series = [
            charts.Series(
                "max sustainable concurrency",
                "var(--agent-1)",
                [
                    (run.started_at, perf.max_sustainable_concurrency)
                    for run, perf in rows
                ],
            )
        ]
        panels.append(
            (
                key,
                rows,
                charts.line_chart(
                    series,
                    y_min=0,
                    y_format=lambda v: f"{v:g}",
                    height=170,
                    width=charts.WIDTH_THIRD,
                    label=f"Max sustainable concurrency for {key}",
                ),
            )
        )
    return render(
        request,
        "performance.html",
        nav="performance",
        panels=panels,
        days=days,
        windows=TREND_WINDOWS,
    )
