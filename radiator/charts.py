"""Server-rendered SVG charts.

Charts are drawn here rather than by a JS library, so pages work without a
frontend build and render the same for everyone. ``static/charts.js`` adds the
hover layer (a crosshair that snaps to the nearest run and a tooltip listing
every series there) from the data embedded in each chart.

Specs follow the house rules: 2px lines, >=8px end dots with a 2px surface
ring, hairline solid gridlines, one y-axis, a legend for 2+ series, and every
chart paired with a table view in its template.
"""

import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from markupsafe import Markup, escape

AGENT_COLOR_SLOTS = {
    "ars": 1,
    "aragorn": 2,
    "shepherd-aragorn": 2,
    "arax": 3,
    "shepherd-arax": 3,
    "biothings-explorer": 4,
    "shepherd-bte": 4,
    "improving-agent": 5,
    "unsecret-agent": 6,
    "cqs": 7,
}


# Approximate on-screen widths of the page layouts, so text renders near 1:1.
WIDTH_FULL = 1180
WIDTH_HALF = 570
WIDTH_THIRD = 360


def agent_color(agent: str) -> str:
    """The CSS color for an agent. Follows the agent, never its position."""
    return f"var(--agent-{AGENT_COLOR_SLOTS.get(agent, 8)})"


@dataclass
class Series:
    name: str
    color: str
    # (x, y) with y None for a gap; x is when the run started
    points: list[tuple[datetime, Optional[float]]] = field(default_factory=list)


def nice_ticks(lo: float, hi: float, count: int = 4) -> list[float]:
    if hi == lo:
        hi = lo + 1
    raw = (hi - lo) / count
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    start = math.floor(lo / step) * step
    ticks = []
    value = start
    while value <= hi + step * 1e-9:
        ticks.append(round(value, 10))
        value += step
    if ticks[-1] < hi:
        ticks.append(round(value, 10))
    return ticks


def _date_ticks(x0: float, x1: float, count: int = 5) -> list[float]:
    if x1 == x0:
        return [x0]
    return [x0 + (x1 - x0) * i / (count - 1) for i in range(count)]


def line_chart(
    series: list[Series],
    *,
    y_format: Callable[[float], str] = lambda v: f"{v:g}",
    y_min: Optional[float] = None,
    y_max: Optional[float] = None,
    invert: bool = False,
    width: int = 720,
    height: int = 220,
    label: str = "",
) -> Markup:
    """A time-series line chart, one line per series, on a single y-axis.

    ``invert`` puts small values at the top, for ranks (1 is best).

    The SVG scales to its container, text included, so ``width`` should be
    about the width it is shown at; the ``WIDTH_*`` constants match the
    layouts the pages use.
    """
    values = [y for s in series for _, y in s.points if y is not None]
    xs = sorted({x for s in series for x, _ in s.points})
    if not values or not xs:
        return Markup('<p class="empty">No data for this range.</p>')

    lo = min(values) if y_min is None else y_min
    hi = max(values) if y_max is None else y_max
    ticks = nice_ticks(lo, hi)
    if y_min is not None and ticks[0] < y_min:
        # eg ranks start at 1: label the floor itself, not the 0 below it
        ticks[0] = y_min
    lo, hi = min(ticks[0], lo), max(ticks[-1], hi)

    left, right, top, bottom = 48, 16, 10, 26
    plot_w, plot_h = width - left - right, height - top - bottom
    t0, t1 = xs[0].timestamp(), xs[-1].timestamp()

    def sx(x: datetime) -> float:
        if t1 == t0:
            return left + plot_w / 2
        return left + (x.timestamp() - t0) / (t1 - t0) * plot_w

    def sy(y: float) -> float:
        frac = (y - lo) / (hi - lo) if hi != lo else 0.5
        if invert:
            frac = 1 - frac
        return top + plot_h - frac * plot_h

    parts = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{escape(label)}" data-chart="line">'
    ]
    for tick in ticks:
        y = sy(tick)
        parts.append(
            f'<line class="grid" x1="{left}" x2="{width - right}" y1="{y:.1f}" y2="{y:.1f}"/>'
            f'<text x="{left - 8}" y="{y + 4:.1f}" text-anchor="end">{escape(y_format(tick))}</text>'
        )
    base_y = top + plot_h
    parts.append(
        f'<line class="baseline" x1="{left}" x2="{width - right}" y1="{base_y}" y2="{base_y}"/>'
    )
    for i, t in enumerate(_date_ticks(t0, t1, 5 if t1 > t0 else 1)):
        x = left + (t - t0) / (t1 - t0) * plot_w if t1 > t0 else left + plot_w / 2
        anchor = (
            "start"
            if i == 0 and t1 > t0
            else ("end" if x >= width - right - 1 else "middle")
        )
        when = datetime.fromtimestamp(t, tz=xs[0].tzinfo).strftime("%b %-d")
        parts.append(
            f'<text x="{x:.1f}" y="{height - 6}" text-anchor="{anchor}">{when}</text>'
        )

    for s in series:
        runs: list[list[tuple[float, float]]] = [[]]
        for x, y in sorted(s.points, key=lambda p: p[0]):
            if y is None:
                runs.append([])
            else:
                runs[-1].append((sx(x), sy(y)))
        for run in runs:
            if len(run) > 1:
                d = " ".join(f"{px:.1f},{py:.1f}" for px, py in run)
                parts.append(
                    f'<polyline class="series" points="{d}" style="stroke:{s.color}"/>'
                )
            elif len(run) == 1:
                px, py = run[0]
                parts.append(
                    f'<circle class="dot" cx="{px:.1f}" cy="{py:.1f}" r="3" style="fill:{s.color}"/>'
                )
        last = next(
            (
                (x, y)
                for x, y in sorted(s.points, key=lambda p: p[0], reverse=True)
                if y is not None
            ),
            None,
        )
        if last is not None:
            parts.append(
                f'<circle class="dot" cx="{sx(last[0]):.1f}" cy="{sy(last[1]):.1f}" r="4" style="fill:{s.color}"/>'
            )

    parts.append(
        f'<line class="crosshair" x1="0" x2="0" y1="{top}" y2="{base_y}" visibility="hidden"/>'
    )
    parts.append(
        f'<rect class="hit" x="{left}" y="{top}" width="{plot_w}" height="{plot_h}" fill="transparent"/>'
    )
    parts.append("</svg>")

    # what the hover layer needs: x positions (in viewBox units) and, at each,
    # every series' formatted value
    hover = {
        "width": width,
        "xs": [round(sx(x), 1) for x in xs],
        "titles": [x.strftime("%b %-d %Y, %H:%M") for x in xs],
        "series": [
            {
                "name": s.name,
                "color": s.color,
                "values": [
                    (lambda y: None if y is None else y_format(y))(
                        dict(s.points).get(x)
                    )
                    for x in xs
                ],
            }
            for s in series
        ],
    }
    legend = ""
    if len(series) > 1:
        legend = (
            '<div class="legend">'
            + "".join(
                f'<span class="key"><span class="line" style="background:{s.color}"></span>{escape(s.name)}</span>'
                for s in series
            )
            + "</div>"
        )
    data = escape(json.dumps(hover))
    return Markup(
        f'<div class="chart-wrap" data-hover="{data}">{"".join(parts)}</div>{legend}'
    )
