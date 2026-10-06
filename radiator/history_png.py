"""A run's pass-rate history as a PNG, for places that can't show the
dashboard: chiefly the harness's Slack report, which can't render SVG or
reach pages behind the login.

Drawn with matplotlib in the dashboard's light palette, following the same
chart rules: one y-axis, 2px lines, hairline grid, each agent's fixed color,
and a legend that carries this run's value, so color never works alone.
"""

import io

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter, MultipleLocator  # noqa: E402

from radiator import queries  # noqa: E402
from radiator.charts import AGENT_COLOR_SLOTS  # noqa: E402

# The light-mode values of the CSS tokens in static/style.css.
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#6f6d68"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
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


def agent_hex(agent: str) -> str:
    return AGENT_HEX[AGENT_COLOR_SLOTS.get(agent, 8)]


def render(run, history: list) -> bytes:
    """``history`` is ``queries.series_history(session, run)``."""
    agents = sorted(
        {agent for _, counts in history for agent in counts},
        key=queries.agent_sort_key,
    )
    times = [r.started_at for r, _ in history]

    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    plt.rcParams["font.family"] = "DejaVu Sans"

    handles, labels = [], []
    for agent in agents:
        rates = [queries.pass_rate(counts.get(agent, {})) for _, counts in history]
        points = [(t, r) for t, r in zip(times, rates) if r is not None]
        if not points:
            continue
        color = agent_hex(agent)
        xs, ys = zip(*points)
        (line,) = ax.plot(
            xs,
            ys,
            color=color,
            linewidth=1.6,
            solid_joinstyle="round",
            solid_capstyle="round",
            zorder=3,
        )
        current = rates[-1]
        if current is not None:
            # this run's value: the end dot, ringed in the surface color
            ax.scatter(
                [times[-1]],
                [current],
                s=34,
                color=color,
                edgecolors=SURFACE,
                linewidths=1.6,
                zorder=4,
            )
        handles.append(line)
        labels.append(f"{agent}  {'–' if current is None else f'{current:.0%}'}")

    # mark this run
    ax.axvline(times[-1], color=AXIS, linewidth=0.8, zorder=1)
    ax.annotate(
        "this run",
        xy=(times[-1], 1.0),
        xytext=(0, 4),
        textcoords="offset points",
        ha="right" if len(times) > 1 else "center",
        va="bottom",
        fontsize=7,
        color=MUTED,
    )

    ax.set_ylim(0, 1.0)
    ax.yaxis.set_major_locator(MultipleLocator(0.25))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0%}"))
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(AXIS)
    ax.spines["bottom"].set_linewidth(0.6)
    ax.tick_params(colors=MUTED, labelsize=7, length=0, pad=4)
    if len(times) > 1:
        span = times[-1] - times[0]
        pad = span * 0.02
        ax.set_xlim(times[0] - pad, times[-1] + pad)
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3, maxticks=7))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %-d"))

    where = run.env or "no environment"
    extra = []
    if run.target:
        extra.append(f"target {run.target}")
    if run.query_type:
        extra.append(f"{run.query_type} only")
    title = f"{run.suite} · {where}" + (f" · {', '.join(extra)}" if extra else "")
    fig.text(0.06, 0.94, title, fontsize=10, fontweight="bold", color=INK)
    fig.text(
        0.06,
        0.885,
        f"Pass rate by agent, last {len(history)} run{'s' if len(history) != 1 else ''} "
        f"to {run.started_at:%b %-d %Y %H:%M} UTC · skips excluded",
        fontsize=7.5,
        color=INK_2,
    )
    if handles:
        legend = fig.legend(
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
        legend.set_zorder(5)
    else:
        ax.text(
            0.5,
            0.5,
            "No agent results in this series",
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
