"""A run's history as PNGs, for places that can't show the dashboard: chiefly
the harness's Slack report, which can't render SVG or reach pages behind the
login.

* ``render``: pass rate by agent over the run's series.
* ``render_performance``: each of the run's services' max sustainable
  concurrency over its series.

Drawn with matplotlib in the dashboard's light palette, following the same
chart rules: one y-axis, step lines (a run's value holds until the next run),
hairline grid, each agent's fixed color, and this run's value written out, so
color never works alone.
"""

import io
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.ticker import FuncFormatter, MaxNLocator, MultipleLocator  # noqa: E402

from radiator import queries  # noqa: E402
from radiator.charts import AGENT_COLOR_SLOTS  # noqa: E402

# The light-mode values of the CSS tokens in static/style.css.
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#6f6d68"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
STATUS_FAILED = "#d03b3b"
AGENT_HEX = {
    1: "#2a78d6",
    2: "#eb6834",
    3: "#1baf7a",
    4: "#eda100",
    5: "#e87ba4",
    6: "#008300",
    7: "#4a3aa7",
    8: "#e34948",
}


def agent_hex(agent: Optional[str]) -> str:
    return AGENT_HEX[AGENT_COLOR_SLOTS.get(agent or "", 8)]


@dataclass
class Line:
    label: str
    color: str
    # one value per run, None where the run had none
    values: list[Optional[float]]
    # runs to flag on the line (eg missed checkpoints), as booleans per run
    flagged: Optional[list[bool]] = None


def _step_segments(times, values):
    """Unbroken stretches of values: a run with no value is a gap, rather
    than the step before it carrying on across it."""
    segment = []
    for t, v in zip(times, values):
        if v is None:
            if segment:
                yield segment
            segment = []
        else:
            segment.append((t, v))
    if segment:
        yield segment


def _chart(
    times: list[datetime],
    lines: list[Line],
    *,
    title: str,
    subtitle: str,
    y_format: Callable[[float], str],
    y_max: Optional[float] = None,
    value_format: Callable[[float], str],
    flag_label: str = "",
    empty_text: str = "No data in this series",
) -> bytes:
    plt.rcParams["font.family"] = "DejaVu Sans"
    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    handles, labels = [], []
    any_flag = False
    for line in lines:
        drawn = False
        for segment in _step_segments(times, line.values):
            xs, ys = zip(*segment)
            ax.plot(
                xs,
                ys,
                drawstyle="steps-post",
                color=line.color,
                linewidth=1.6,
                solid_joinstyle="miter",
                zorder=3,
            )
            drawn = True
        if not drawn:
            continue
        if line.flagged:
            flagged = [
                (t, v)
                for t, v, f in zip(times, line.values, line.flagged)
                if f and v is not None
            ]
            if flagged:
                any_flag = True
                fx, fy = zip(*flagged)
                ax.scatter(
                    fx,
                    fy,
                    marker="x",
                    s=26,
                    color=STATUS_FAILED,
                    linewidths=1.4,
                    zorder=5,
                )
        current = line.values[-1]
        if current is not None:
            # this run's value: the end dot, ringed in the surface color
            ax.scatter(
                [times[-1]],
                [current],
                s=34,
                color=line.color,
                edgecolors=SURFACE,
                linewidths=1.6,
                zorder=4,
            )
        handles.append(Line2D([], [], color=line.color, linewidth=1.6))
        labels.append(
            f"{line.label}  {'–' if current is None else value_format(current)}"
        )
    if any_flag:
        handles.append(
            Line2D(
                [],
                [],
                marker="x",
                linestyle="",
                color=STATUS_FAILED,
                markersize=5,
                markeredgewidth=1.4,
            )
        )
        labels.append(flag_label)

    # mark this run
    ax.axvline(times[-1], color=AXIS, linewidth=0.8, zorder=1)
    ax.annotate(
        "this run",
        xy=(1.0, 1.0),
        xycoords="axes fraction",
        xytext=(0, 4),
        textcoords="offset points",
        ha="right",
        va="bottom",
        fontsize=7,
        color=MUTED,
    )

    top = y_max
    if top is None:
        values = [v for line in lines for v in line.values if v is not None]
        top = max(values) * 1.15 if values and max(values) > 0 else 1
    ax.set_ylim(0, top)
    if y_max == 1.0:
        ax.yaxis.set_major_locator(MultipleLocator(0.25))
    else:
        ax.yaxis.set_major_locator(MaxNLocator(nbins=5, integer=False))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: y_format(v)))
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(AXIS)
    ax.spines["bottom"].set_linewidth(0.6)
    ax.tick_params(colors=MUTED, labelsize=7, length=0, pad=4)
    if len(times) > 1:
        pad = (times[-1] - times[0]) * 0.02
        ax.set_xlim(times[0] - pad, times[-1] + pad)
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3, maxticks=7))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %-d"))

    if len(lines) == 1 and len(handles) == 1:
        # one series: the title already names it, so no legend; its value
        # goes in the subtitle instead
        current = lines[0].values[-1]
        if current is not None:
            subtitle += f" · this run {value_format(current)}"
        handles, labels = [], []
    fig.text(0.06, 0.94, title, fontsize=10, fontweight="bold", color=INK)
    fig.text(0.06, 0.885, subtitle, fontsize=7.5, color=INK_2)
    if handles:
        fig.legend(
            handles,
            labels,
            loc="lower left",
            bbox_to_anchor=(0.055, 0.0),
            ncol=min(len(handles), 4),
            frameon=False,
            fontsize=7.5,
            handlelength=1.4,
            columnspacing=1.6,
            labelcolor=INK_2,
        )
    elif not any(v is not None for line in lines for v in line.values):
        ax.text(
            0.5,
            0.5,
            empty_text,
            transform=ax.transAxes,
            ha="center",
            color=MUTED,
            fontsize=8,
        )

    rows = (len(handles) + 3) // 4 if handles else 0
    fig.subplots_adjust(left=0.07, right=0.97, top=0.84, bottom=0.12 + 0.05 * rows)
    out = io.BytesIO()
    fig.savefig(out, format="png", facecolor=SURFACE)
    plt.close(fig)
    return out.getvalue()


def _runs_phrase(n: int, until: datetime) -> str:
    return f"last {n} run{'s' if n != 1 else ''} to {until:%b %-d %Y %H:%M} UTC"


def render(run, history: list) -> bytes:
    """Pass rate by agent. ``history`` is ``queries.series_history(...)``."""
    agents = sorted(
        {agent for _, counts in history for agent in counts},
        key=queries.agent_sort_key,
    )
    lines = [
        Line(
            agent,
            agent_hex(agent),
            [queries.pass_rate(counts.get(agent, {})) for _, counts in history],
        )
        for agent in agents
    ]
    extra = []
    if run.target:
        extra.append(f"target {run.target}")
    if run.query_type:
        extra.append(f"{run.query_type} only")
    title = f"{run.suite} · {run.env or 'no environment'}" + (
        f" · {', '.join(extra)}" if extra else ""
    )
    return _chart(
        [r.started_at for r, _ in history],
        lines,
        title=title,
        subtitle=(
            f"Pass rate by agent, {_runs_phrase(len(history), run.started_at)}"
            " · skips excluded"
        ),
        y_format=lambda v: f"{v:.0%}",
        y_max=1.0,
        value_format=lambda v: f"{v:.0%}",
        empty_text="No agent results in this series",
    )


def render_performance(run, series: list) -> bytes:
    """Max sustainable concurrency for each service in the run.

    ``series`` is ``queries.performance_series(...)``: per service, its
    history up to this run. Services can have different histories, so the
    x-axis is the union of their runs.
    """
    times = sorted({r.started_at for _, history in series for r, _ in history})
    lines = []
    for perf, history in series:
        by_time = {r.started_at: p for r, p in history}
        name = perf.helmsdeep_target or perf.host
        lines.append(
            Line(
                name,
                agent_hex(perf.helmsdeep_target),
                [
                    by_time[t].max_sustainable_concurrency if t in by_time else None
                    for t in times
                ],
                flagged=[
                    t in by_time and by_time[t].checkpoints_passed is False
                    for t in times
                ],
            )
        )
    names = ", ".join(p.helmsdeep_target or p.host for p, _ in series)
    profiles = sorted({p.profile or "default" for p, _ in series})
    longest = max((len(h) for _, h in series), default=0)
    return _chart(
        times,
        lines,
        title=f"{names} · {run.env or 'no environment'}",
        subtitle=(
            "Max sustainable concurrency (users within the SLO), "
            f"{_runs_phrase(longest, run.started_at)} · profile {', '.join(profiles)}"
        ),
        y_format=lambda v: f"{v:g}",
        value_format=lambda v: f"{v:.1f}",
        flag_label="checkpoints missed",
        empty_text="No stage met the SLO in these runs",
    )
