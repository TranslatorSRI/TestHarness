"""The acceptance run grid: every run of a suite, by environment, over time.

A lane per environment, time across. Each run is a card (number, start time,
pass rate and its change, passed / failed / skipped) whose trend compares its
pass rate with the previous run in the same environment: UP, DOWN, FLAT, or
BASE for the first one shown. Excluded environments (dev, by default) are drawn
greyed out, without a trend. Runs too close in time to sit side by side stack
downward, and each run is joined to the one before it in its lane.

The layout is computed here and drawn as SVG for the dashboard (cards link
to their runs, colors follow the page's theme).
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from markupsafe import Markup, escape
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from radiator.models import AssetResult, Run

# Lanes in this order; any other environment after these, alphabetically, and
# runs with no environment last.
ENV_ORDER = ["dev", "ci", "test", "prod"]
NOT_PASSING = ("FAILED", "NO_RESULTS", "ERROR")

UP, DOWN, FLAT, BASE, EXCLUDED, NO_DATA = "up", "down", "flat", "base", "excl", "nodata"
TREND_LABELS = {
    UP: "UP",
    DOWN: "DOWN",
    FLAT: "FLAT",
    BASE: "BASE",
    EXCLUDED: "EXCL",
    NO_DATA: "N/A",
}

# Geometry, in px of the drawing.
CARD_W, CARD_H = 188, 92
GAP_X, GAP_Y = 14, 14
LANE_PAD = 20
LABEL_W = 150
RIGHT_PAD = 24
AXIS_H = 40


@dataclass
class GridRun:
    run: Run
    passed: int
    failed: int
    skipped: int
    rate: Optional[float]
    trend: str = BASE
    delta: Optional[float] = None  # percentage points vs the previous run


@dataclass
class Card:
    item: GridRun
    x: float  # top left
    y: float
    level: int


@dataclass
class Lane:
    env: Optional[str]
    excluded: bool
    y: float
    height: float
    cards: list[Card] = field(default_factory=list)

    @property
    def label(self) -> str:
        return (self.env or "no environment").upper()


@dataclass
class Link:
    x1: float
    y1: float
    x2: float
    y2: float
    trend: str


@dataclass
class Grid:
    suite: str
    since: datetime
    until: datetime
    width: float
    height: float  # lanes + time axis
    lanes: list[Lane]
    links: list[Link]
    ticks: list[tuple[float, str]]

    @property
    def empty(self) -> bool:
        return not self.lanes


# --- data ------------------------------------------------------------------------


def grid_runs(
    session: Session,
    suite: str,
    since: datetime,
    until: datetime,
    excluded_envs: Iterable[str] = ("dev",),
) -> list[GridRun]:
    """The suite's finished, full acceptance runs in the window, oldest first,
    each with its trend against the previous run in its environment.

    "Full" leaves out runs with a target override or a single query type,
    which measure something else. The run before the window is fetched too,
    so the first card shown still has a trend rather than starting over.
    """
    full = [
        Run.suite == suite,
        Run.target.is_(None),
        Run.query_type.is_(None),
        Run.ended_at.is_not(None),
        Run.started_at <= until,
    ]
    runs = list(
        session.scalars(
            select(Run).where(*full, Run.started_at >= since).order_by(Run.started_at)
        )
    )
    # one run per environment from just before the window, as a baseline
    envs = {run.env for run in runs}
    baselines = []
    for env in envs:
        before = session.scalar(
            select(Run)
            .where(*full, Run.env.is_not_distinct_from(env), Run.started_at < since)
            .order_by(Run.started_at.desc())
            .limit(1)
        )
        if before is not None:
            baselines.append(before)

    all_runs = baselines + runs
    counts: dict = {run.id: {} for run in all_runs}
    if all_runs:
        rows = session.execute(
            select(AssetResult.run_id, AssetResult.status, func.count())
            .where(AssetResult.run_id.in_(list(counts)))
            .group_by(AssetResult.run_id, AssetResult.status)
        )
        for run_id, status, n in rows:
            counts[run_id][status] = n

    excluded = {e.lower() for e in excluded_envs}
    previous_rate: dict = {}
    items = []
    for run in sorted(all_runs, key=lambda r: r.started_at):
        c = counts[run.id]
        if not c:
            continue  # no acceptance results (eg a performance run)
        passed = c.get("PASSED", 0)
        failed = sum(c.get(s, 0) for s in NOT_PASSING)
        skipped = c.get("SKIPPED", 0)
        rate = passed / (passed + failed) if passed + failed else None
        item = GridRun(run, passed, failed, skipped, rate)
        prev = previous_rate.get(run.env)
        if (run.env or "").lower() in excluded:
            item.trend = EXCLUDED
        elif rate is None:
            item.trend = NO_DATA
        elif prev is None:
            item.trend = BASE
        else:
            item.delta = (rate - prev) * 100
            # FLAT when equal to a tenth of a point
            if round(rate * 1000) == round(prev * 1000):
                item.trend = FLAT
            else:
                item.trend = UP if rate > prev else DOWN
        if rate is not None:
            previous_rate[run.env] = rate
        if run.started_at >= since:
            items.append(item)
    return items


# --- layout ----------------------------------------------------------------------


def _env_key(env: Optional[str]):
    if env in ENV_ORDER:
        return (0, ENV_ORDER.index(env), "")
    return (1 if env else 2, 0, env or "")


def _ticks(since: datetime, until: datetime) -> list[datetime]:
    """At midnight: daily for a short window, every other day for a few
    weeks, and on Mondays beyond that."""
    span_days = (until - since).total_seconds() / 86400
    step = 1 if span_days <= 10 else 2 if span_days <= 21 else 7
    first = since.replace(hour=0, minute=0, second=0, microsecond=0)
    if first < since:
        first += timedelta(days=1)
    if step == 7:
        first += timedelta(days=(7 - first.weekday()) % 7)
    ticks = []
    t = first
    while t <= until:
        ticks.append(t)
        t += timedelta(days=step)
    return ticks


def layout(
    suite: str,
    items: list[GridRun],
    since: datetime,
    until: datetime,
    width: float,
    excluded_envs: Iterable[str] = ("dev",),
) -> Grid:
    plot_left, plot_right = LABEL_W, width - RIGHT_PAD
    span = (until - since).total_seconds() or 1

    def center_x(t: datetime) -> float:
        frac = min(max((t - since).total_seconds() / span, 0), 1)
        return plot_left + CARD_W / 2 + frac * (plot_right - plot_left - CARD_W)

    def time_x(t: datetime) -> float:
        frac = (t - since).total_seconds() / span
        return plot_left + CARD_W / 2 + frac * (plot_right - plot_left - CARD_W)

    excluded = {e.lower() for e in excluded_envs}
    by_env: dict = {}
    for item in items:
        by_env.setdefault(item.run.env, []).append(item)

    lanes, links = [], []
    y = 0.0
    for env in sorted(by_env, key=_env_key):
        lane_items = sorted(by_env[env], key=lambda i: i.run.started_at)
        # greedy stacking: each card on the highest level it fits on
        level_right: list[float] = []
        cards = []
        for item in lane_items:
            x = center_x(item.run.started_at) - CARD_W / 2
            level = next(
                (i for i, right in enumerate(level_right) if x >= right + GAP_X),
                len(level_right),
            )
            if level == len(level_right):
                level_right.append(x + CARD_W)
            else:
                level_right[level] = x + CARD_W
            cards.append(Card(item, x, 0, level))
        levels = max(len(level_right), 1)
        height = LANE_PAD * 2 + levels * CARD_H + (levels - 1) * GAP_Y
        for card in cards:
            card.y = y + LANE_PAD + card.level * (CARD_H + GAP_Y)
        for a, b in zip(cards, cards[1:]):
            links.append(
                Link(
                    a.x + CARD_W / 2,
                    a.y + CARD_H / 2,
                    b.x + CARD_W / 2,
                    b.y + CARD_H / 2,
                    b.item.trend,
                )
            )
        lanes.append(Lane(env, (env or "").lower() in excluded, y, height, cards))
        y += height

    ticks = [(time_x(t), t.strftime("%b %-d")) for t in _ticks(since, until)]
    return Grid(suite, since, until, width, y + AXIS_H, lanes, links, ticks)


def build(
    session: Session,
    suite: str,
    until: Optional[datetime] = None,
    days: int = 35,
    width: float = 1180,
    excluded_envs: Iterable[str] = ("dev",),
) -> Grid:
    """The grid for ``suite`` over the ``days`` before ``until`` (now)."""
    until = until or datetime.now(timezone.utc)
    since = until - timedelta(days=days)
    items = grid_runs(session, suite, since, until, excluded_envs)
    return layout(suite, items, since, until, width, excluded_envs)


# --- card text -----------------------------------------------------------------


def card_lines(item: GridRun) -> tuple[str, str, str, str]:
    """(number, start time, rate line, counts line)."""
    rate = "all skipped" if item.rate is None else f"{item.rate * 100:.1f}%"
    if item.delta is not None and item.trend != FLAT:
        rate += f"  {'▲' if item.delta > 0 else '▼'}{abs(item.delta):.1f} pts"
    elif item.trend == FLAT:
        rate += "  no change"
    counts = f"P {item.passed}  ·  F {item.failed}  ·  S {item.skipped}"
    return (
        f"#{item.run.number}",
        item.run.started_at.astimezone(timezone.utc).strftime("%m/%d %H:%M"),
        rate,
        counts,
    )


def card_tip(item: GridRun) -> str:
    number, when, rate, _ = card_lines(item)
    lines = [
        f"{number} · {item.run.env or 'no environment'} · {when} UTC",
        f"{TREND_LABELS[item.trend]} · pass rate {rate}",
        f"{item.passed} passed, {item.failed} not passing, {item.skipped} skipped",
    ]
    return "\n".join(lines)


# --- SVG ---------------------------------------------------------------------------


def to_svg(grid: Grid) -> Markup:
    """The grid as SVG, colored by the page's CSS tokens; cards link to runs."""
    w, h = grid.width, grid.height
    parts = [
        f'<svg class="run-grid" viewBox="0 0 {w:.0f} {h:.0f}" role="img" '
        f'aria-label="Acceptance runs of {escape(grid.suite)} by environment over time">'
    ]
    for i, lane in enumerate(grid.lanes):
        band = "band-alt" if i % 2 else "band"
        parts.append(
            f'<rect class="{band}" x="0" y="{lane.y:.1f}" width="{w:.0f}" height="{lane.height:.1f}"/>'
        )
        label_y = lane.y + lane.height / 2
        parts.append(
            f'<text class="lane-label" x="16" y="{label_y + 5:.1f}">{escape(lane.label)}</text>'
        )
        if lane.excluded:
            parts.append(
                f'<text class="lane-note" x="16" y="{label_y + 22:.1f}">excluded</text>'
            )
    lanes_bottom = grid.height - AXIS_H
    for x, label in grid.ticks:
        parts.append(
            f'<line class="tick" x1="{x:.1f}" x2="{x:.1f}" y1="0" y2="{lanes_bottom:.1f}"/>'
            f'<text class="tick-label" x="{x:.1f}" y="{lanes_bottom + 24:.1f}" text-anchor="middle">{label}</text>'
        )
    for link in grid.links:
        parts.append(
            f'<line class="link t-{link.trend}" x1="{link.x1:.1f}" y1="{link.y1:.1f}" '
            f'x2="{link.x2:.1f}" y2="{link.y2:.1f}"/>'
        )
    for lane in grid.lanes:
        for card in lane.cards:
            parts.append(_svg_card(card))
    parts.append("</svg>")
    return Markup("".join(parts))


def _svg_card(card: Card) -> str:
    item, x, y = card.item, card.x, card.y
    number, when, rate, counts = card_lines(item)
    trend = item.trend
    label = TREND_LABELS[trend]
    chip_w = 10 + 7.5 * len(label)
    return (
        f'<a href="/runs/{item.run.id}" data-tip="{escape(card_tip(item))}">'
        f'<g class="card t-{trend}">'
        f'<rect class="card-box" x="{x:.1f}" y="{y:.1f}" width="{CARD_W}" height="{CARD_H}" rx="10"/>'
        f'<rect class="chip" x="{x + 10:.1f}" y="{y + 9:.1f}" width="{chip_w:.1f}" height="17" rx="4"/>'
        f'<text class="chip-label" x="{x + 10 + chip_w / 2:.1f}" y="{y + 21:.1f}" text-anchor="middle">{label}</text>'
        f'<text class="number" x="{x + CARD_W - 12:.1f}" y="{y + 22:.1f}" text-anchor="end">{escape(number)}</text>'
        f'<text class="when" x="{x + 12:.1f}" y="{y + 43:.1f}">{escape(when)} UTC</text>'
        f'<text class="rate" x="{x + 12:.1f}" y="{y + 63:.1f}">{escape(rate)}</text>'
        f'<text class="counts" x="{x + 12:.1f}" y="{y + 82:.1f}">{escape(counts)}</text>'
        "</g></a>"
    )
