"""The acceptance run grid: every run of a suite, by environment, over time.

A lane per environment, time across. Each run is a card (number, start time,
pass rate and its change, passed / failed / skipped) whose trend compares its
pass rate with the previous run in the same environment: UP, DOWN, FLAT, or
BASE for the first one shown. Excluded environments (dev, by default) are drawn
greyed out, without a trend. Runs too close in time to sit side by side stack
downward, and each run is joined to the one before it in its lane.

The layout is computed once here and drawn twice: as SVG for the dashboard
(cards link to their runs, colors follow the page's theme), and as a PNG for
the harness's Slack report.
"""

import io
import math
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
    new: bool = False


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
    new_run_id=None,
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
        item = GridRun(run, passed, failed, skipped, rate, new=run.id == new_run_id)
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


def fit_window(
    items: list[GridRun], since: datetime, until: datetime, max_per_lane: int
) -> datetime:
    """Move ``since`` forward until no environment has more than
    ``max_per_lane`` runs in the window, but keep at least a week: an
    environment run daily would otherwise stack its cards into a wall."""
    floor = until - timedelta(days=7)
    by_env: dict = {}
    for item in items:
        by_env.setdefault(item.run.env, []).append(item.run.started_at)
    for starts in by_env.values():
        starts.sort()
        if len(starts) > max_per_lane:
            # just before the oldest run that still fits
            since = max(since, starts[-max_per_lane] - timedelta(hours=6))
    return min(since, floor)


def build(
    session: Session,
    suite: str,
    until: Optional[datetime] = None,
    days: int = 35,
    width: float = 1180,
    excluded_envs: Iterable[str] = ("dev",),
    new_run_id=None,
    max_per_lane: Optional[int] = None,
) -> Grid:
    """The grid for ``suite`` over the ``days`` before ``until`` (now).

    With ``max_per_lane``, the window narrows to fit (see ``fit_window``).
    Trends come from the full history either way, so a narrowed window's
    first card still compares with the run before it.
    """
    until = until or datetime.now(timezone.utc)
    since = until - timedelta(days=days)
    items = grid_runs(session, suite, since, until, excluded_envs, new_run_id)
    if max_per_lane:
        since = fit_window(items, since, until, max_per_lane)
        items = [item for item in items if item.run.started_at >= since]
    return layout(suite, items, since, until, width, excluded_envs)


# --- card text (shared by both drawings) ----------------------------------------


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


# --- SVG (dashboard) ----------------------------------------------------------------


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
    new = ""
    if item.new:
        new = (
            f'<rect class="new" x="{x + CARD_W - 46:.1f}" y="{y - 9:.1f}" width="40" height="16" rx="8"/>'
            f'<text class="new-label" x="{x + CARD_W - 26:.1f}" y="{y + 3:.1f}" text-anchor="middle">NEW</text>'
        )
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
        f"{new}</g></a>"
    )


# --- PNG (Slack) --------------------------------------------------------------------

# Light-mode values of the page's tokens (static/style.css).
_INK, _INK_2, _MUTED = "#0b0b0b", "#52514e", "#6f6d68"
_SURFACE, _BAND, _BAND_ALT = "#fcfcfb", "#fcfcfb", "#f3f2ef"
_GRID, _AXIS = "#e1e0d9", "#c3c2b7"
_TREND = {
    UP: "#0ca30c",
    DOWN: "#d03b3b",
    FLAT: "#fab219",
    BASE: "#898781",
    EXCLUDED: "#b5b3ab",
    NO_DATA: "#b5b3ab",
}
_HEADER_BG = "#17202b"
TITLE_H, FOOTER_H = 92, 44


def _tint(hex_color: str, amount: float, base: str = _SURFACE) -> str:
    """``hex_color`` mixed into ``base`` (amount 0..1)."""
    a = [int(hex_color[i : i + 2], 16) for i in (1, 3, 5)]
    b = [int(base[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(
        f"{round(bb + (aa - bb) * amount):02x}" for aa, bb in zip(a, b)
    )


def to_png(grid: Grid, title: str, subtitle: str) -> bytes:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch, Rectangle

    plt.rcParams["font.family"] = "DejaVu Sans"
    W = grid.width
    H = TITLE_H + max(grid.height, 120) + FOOTER_H
    dpi = 100
    fig = plt.figure(figsize=(W / dpi, H / dpi), dpi=dpi)
    fig.patch.set_facecolor(_SURFACE)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    ax.axis("off")

    def text(x, y, s, size, color=_INK, weight="normal", ha="left", va="baseline"):
        # above every patch, so a card's text is never under its box
        ax.text(
            x,
            y,
            s,
            fontsize=size * 72 / dpi,
            color=color,
            fontweight=weight,
            ha=ha,
            va=va,
            zorder=6,
        )

    # title bar, with a legend that names every color (labels, not color alone)
    ax.add_patch(
        FancyBboxPatch(
            (12, 10),
            W - 24,
            TITLE_H - 22,
            boxstyle="round,pad=0,rounding_size=14",
            facecolor=_HEADER_BG,
            edgecolor="none",
        )
    )
    text(36, 46, title, 26, "#ffffff", "bold")
    text(36, 72, subtitle, 14, "#c3c2b7")
    lx = W - 520
    for trend in (UP, DOWN, FLAT, BASE, EXCLUDED):
        ax.add_patch(
            Rectangle((lx, 41), 26, 5, facecolor=_TREND[trend], edgecolor="none")
        )
        text(lx + 34, 48, TREND_LABELS[trend], 13, "#ffffff", "bold")
        lx += 100

    oy = TITLE_H
    if grid.empty:
        text(
            W / 2,
            oy + 60,
            "No finished full runs of this suite in this window",
            15,
            _MUTED,
            ha="center",
        )
    for i, lane in enumerate(grid.lanes):
        ax.add_patch(
            Rectangle(
                (0, oy + lane.y),
                W,
                lane.height,
                facecolor=_BAND_ALT if i % 2 else _BAND,
                edgecolor="none",
            )
        )
        cy = oy + lane.y + lane.height / 2
        text(20, cy + 6, lane.label, 18, _INK, "bold")
        if lane.excluded:
            text(20, cy + 26, "excluded", 12, _MUTED)
    lanes_bottom = oy + grid.height - AXIS_H
    for x, label in grid.ticks:
        ax.plot([x, x], [oy, lanes_bottom], color=_GRID, linewidth=1, zorder=1)
        text(x, lanes_bottom + 26, label, 13, _MUTED, ha="center")
    for link in grid.links:
        dashed = link.trend in (EXCLUDED, NO_DATA)
        ax.plot(
            [link.x1, link.x2],
            [oy + link.y1, oy + link.y2],
            color=_TREND[link.trend],
            linewidth=4 if not dashed else 3,
            linestyle=(0, (2, 2)) if dashed else "solid",
            zorder=2,
            solid_capstyle="butt",
        )
    for lane in grid.lanes:
        for card in lane.cards:
            _png_card(ax, text, card, oy, FancyBboxPatch)
    text(
        LABEL_W,
        H - 16,
        "Trend compares each run's pass rate (skips left out) with the previous run "
        "in the same environment; FLAT is within 0.1 pts.",
        12,
        _MUTED,
    )

    out = io.BytesIO()
    fig.savefig(out, format="png", dpi=dpi, facecolor=_SURFACE)
    plt.close(fig)
    return out.getvalue()


def _png_card(ax, text, card: Card, oy: float, FancyBboxPatch) -> None:
    item = card.item
    x, y = card.x, oy + card.y
    color = _TREND[item.trend]
    muted = item.trend in (EXCLUDED, NO_DATA)
    ax.add_patch(
        FancyBboxPatch(
            (x, y),
            CARD_W,
            CARD_H,
            boxstyle="round,pad=0,rounding_size=10",
            facecolor=_tint(color, 0.08 if muted else 0.12),
            edgecolor=color,
            linewidth=2.2,
            linestyle=(0, (4, 3)) if muted else "solid",
            zorder=3,
        )
    )
    number, when, rate, counts = card_lines(item)
    label = TREND_LABELS[item.trend]
    chip_w = 10 + 8 * len(label)
    ax.add_patch(
        FancyBboxPatch(
            (x + 10, y + 9),
            chip_w,
            18,
            boxstyle="round,pad=0,rounding_size=4",
            facecolor=color,
            edgecolor="none",
            zorder=4,
        )
    )
    text(
        x + 10 + chip_w / 2,
        y + 22.5,
        label,
        11.5,
        "#ffffff" if item.trend == DOWN else _INK,
        "bold",
        ha="center",
    )
    text(x + CARD_W - 12, y + 23, number, 14, _INK, "bold", ha="right")
    text(x + 12, y + 44, f"{when} UTC", 12.5, _INK_2)
    text(x + 12, y + 64, rate, 13.5, _INK, "bold")
    text(x + 12, y + 83, counts, 12, _INK_2)
    if item.new:
        ax.add_patch(
            FancyBboxPatch(
                (x + CARD_W - 50, y - 10),
                42,
                18,
                boxstyle="round,pad=0,rounding_size=9",
                facecolor=_HEADER_BG,
                edgecolor="none",
                zorder=5,
            )
        )
        text(x + CARD_W - 29, y + 3.5, "NEW", 11, "#ffffff", "bold", ha="center")
