"""The cycle board and the weekly digest, drawn as PNGs for Slack.

Both are tables first: a row per test (the board) or per environment (the
digest), with the headline number, its change against the previous run, and a
verdict that always comes as a word beside its color. The board adds a small
step line of each test's last runs, so a one-off dip reads differently from a
slide.
"""

import io
from datetime import datetime, timezone
from typing import Optional

from radiator import cycles
from radiator.cycles import Cycle, Digest, Row

# Light-mode values of the page's tokens (static/style.css).
INK, INK_2, MUTED = "#0b0b0b", "#52514e", "#6f6d68"
SURFACE, BAND, RULE = "#fcfcfb", "#f3f2ef", "#e1e0d9"
# Status colors, reserved for verdicts and never used for anything else.
VERDICT_COLORS = {
    cycles.REGRESSED: "#d03b3b",
    cycles.MIXED: "#eda100",
    cycles.IMPROVED: "#0ca30c",
    cycles.STEADY: "#898781",
    cycles.FIRST: "#2a78d6",
    cycles.NO_DATA: "#52514e",
}
FLAG = "⚑"


class _Canvas:
    """A figure in px, y down, with text always above the shapes."""

    def __init__(self, width: float, height: float):
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        plt.rcParams["font.family"] = "DejaVu Sans"
        self.plt = plt
        self.width, self.height = width, height
        self.fig = plt.figure(figsize=(width / 100, height / 100), dpi=100)
        self.fig.patch.set_facecolor(SURFACE)
        self.ax = self.fig.add_axes([0, 0, 1, 1])
        self.ax.set_xlim(0, width)
        self.ax.set_ylim(height, 0)
        self.ax.axis("off")

    def text(self, x, y, s, size, color=INK, weight="normal", ha="left", va="baseline"):
        return self.ax.text(
            x,
            y,
            s,
            fontsize=size * 0.75,
            color=color,
            weight=weight,
            ha=ha,
            va=va,
            zorder=6,
        )

    def width_of(self, artist) -> float:
        return artist.get_window_extent(renderer=self.fig.canvas.get_renderer()).width

    def rule(self, y, x1=32, x2=None):
        self.ax.plot([x1, x2 or self.width - 32], [y, y], color=RULE, lw=1, zorder=1)

    def band(self, y, h):
        from matplotlib.patches import Rectangle

        self.ax.add_patch(
            Rectangle((0, y), self.width, h, facecolor=BAND, edgecolor="none", zorder=0)
        )

    def dot(self, x, y, r, color, ring=SURFACE, lw=2.0):
        from matplotlib.patches import Circle

        self.ax.add_patch(
            Circle((x, y), r, facecolor=color, edgecolor=ring, lw=lw, zorder=5)
        )

    def verdict(self, x, y, verdict, size=14):
        """A verdict: its color, then its word."""
        self.dot(x + 7, y - size * 0.32, 7, VERDICT_COLORS[verdict], lw=0)
        return self.text(x + 20, y, cycles.VERDICT_LABELS[verdict], size, INK, "bold")

    def legend(self, y, verdicts):
        x = self.width - 32
        for verdict in reversed(verdicts):
            t = self.text(x, y, cycles.VERDICT_LABELS[verdict], 13, INK_2, ha="right")
            w = self.width_of(t)
            self.dot(x - w - 12, y - 4.5, 6, VERDICT_COLORS[verdict], lw=0)
            x -= w + 42

    def png(self) -> bytes:
        buf = io.BytesIO()
        self.fig.savefig(buf, format="png", dpi=100, facecolor=SURFACE)
        self.plt.close(self.fig)
        return buf.getvalue()


def _day(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%a %b %-d")


def _sparkline(c: _Canvas, x0, x1, y0, y1, values: list[Optional[float]], pct: bool):
    """A step line of the last runs, oldest first; this run is the dark dot."""
    points = [(i, v) for i, v in enumerate(values) if v is not None]
    if not points:
        c.text(
            (x0 + x1) / 2, (y0 + y1) / 2, "no data", 12, MUTED, ha="center", va="center"
        )
        return
    lo, hi = min(v for _, v in points), max(v for _, v in points)
    # keep small wobbles small: at least 10 pts (or 20% of the value) of range
    floor = 0.10 if pct else max(hi * 0.2, 1.0)
    if hi - lo < floor:
        mid = (hi + lo) / 2
        lo, hi = mid - floor / 2, mid + floor / 2
    n = max(len(values), 2)

    def px(i):
        return x0 + i * (x1 - x0) / (n - 1)

    def py(v):
        return y1 - (v - lo) / (hi - lo) * (y1 - y0)

    for (i1, v1), (i2, v2) in zip(points, points[1:]):
        c.ax.plot(
            [px(i1), px(i2), px(i2)],
            [py(v1), py(v1), py(v2)],
            color=INK_2,
            lw=1.6,
            zorder=3,
            solid_joinstyle="miter",
        )
    for i, v in points[:-1]:
        c.dot(px(i), py(v), 3.2, INK_2, lw=1.5)
    last_i, last_v = points[-1]
    c.dot(px(last_i), py(last_v), 5.5, INK, lw=2)


BOARD_W = 1400
BOARD_COLS = {
    "test": 32,
    "result": 330,
    "change": 520,
    "spark": 790,
    "verdict": 1095,
    "flags": 1260,
}
BOARD_ROW = 78
BOARD_TOP = 150


def board_png(cycle: Cycle) -> bytes:
    rows = cycle.rows
    env = cycle.env or "mixed environments"
    height = BOARD_TOP + BOARD_ROW * max(len(rows), 1) + 64
    c = _Canvas(BOARD_W, height)
    c.text(
        32, 44, f"{env.upper()} run cycle · {_day(cycle.started_at)}", 24, weight="bold"
    )
    took = ""
    if cycle.ended_at is not None:
        minutes = (cycle.ended_at - cycle.started_at).total_seconds() / 60
        took = f" · took {int(minutes // 60)} h {int(minutes % 60):02d} min"
    c.text(
        32,
        72,
        f"Each test against its previous run in {env}{took}",
        14,
        INK_2,
    )
    c.legend(44, [cycles.REGRESSED, cycles.MIXED, cycles.IMPROVED, cycles.STEADY])

    cols = BOARD_COLS
    head_y = BOARD_TOP - 16
    for key, title in [
        ("test", "Test"),
        ("result", "Result"),
        ("change", "Change"),
        ("spark", f"Last {cycles.HISTORY_RUNS} runs"),
        ("verdict", "Verdict"),
        ("flags", "Flags"),
    ]:
        c.text(cols[key], head_y, title, 12, MUTED)
    c.rule(BOARD_TOP - 6)
    if not rows:
        c.text(32, BOARD_TOP + 40, "No results in this cycle yet.", 15, INK_2)
    for i, row in enumerate(rows):
        y0 = BOARD_TOP + i * BOARD_ROW
        cy = y0 + BOARD_ROW / 2
        c.text(cols["test"], cy - 4, row.label, 16, weight="bold")
        c.text(cols["test"], cy + 18, f"{row.run.suite} · #{row.run.number}", 12, MUTED)
        c.text(cols["result"], cy + 2, row.value_text, 24, weight="bold")
        c.text(cols["result"], cy + 22, row.unit, 12, MUTED)
        c.text(cols["change"], cy - 4, row.change_text, 15, weight="bold")
        c.text(cols["change"], cy + 18, row.detail_text, 12, INK_2)
        _sparkline(
            c,
            cols["spark"],
            cols["spark"] + 250,
            y0 + 16,
            y0 + BOARD_ROW - 16,
            row.history,
            pct=row.perf is None,
        )
        c.verdict(cols["verdict"], cy + 5, row.verdict)
        if row.flags:
            c.text(cols["flags"], cy + 5, f"{FLAG} {len(row.flags)}", 15, INK, "bold")
        else:
            c.text(cols["flags"], cy + 5, "–", 15, MUTED)
        c.rule(y0 + BOARD_ROW)
    c.text(
        32,
        height - 22,
        "Regressions and fixes count agent results that went from passing to not "
        "passing, or back. Flags are listed in the message.",
        12,
        MUTED,
    )
    return c.png()


DIGEST_W = 1500
DIGEST_LABEL_W = 150
DIGEST_FLAGS_W = 110
DIGEST_ENTRY = 50
DIGEST_TOP = 150


def _entry(c: _Canvas, x, y, row: Row, perf_name: bool):
    """One test in a digest cell: verdict, number, change; then detail."""
    c.dot(x + 7, y - 6, 7, VERDICT_COLORS[row.verdict], lw=0)
    head = (
        f"{row.perf.helmsdeep_target or row.perf.host}  "
        if perf_name and row.perf
        else ""
    )
    t = c.text(x + 20, y, f"{head}{row.value_text}", 17, weight="bold")
    c.text(
        x + 28 + c.width_of(t),
        y,
        f"{row.change_text} · {cycles.VERDICT_LABELS[row.verdict]}",
        13,
        INK_2,
    )
    detail = row.detail_text if row.perf is None else row.detail_text
    c.text(
        x + 20,
        y + 19,
        f"{detail} · #{row.run.number}, {_day(row.run.started_at)}",
        11.5,
        MUTED,
    )


def digest_png(d: Digest) -> bytes:
    kinds = cycles.KINDS
    heights = []
    for env in d.envs:
        entries = max([len(env.cell(k)) for k in kinds] + [1])
        heights.append(entries * DIGEST_ENTRY + 30)
    height = DIGEST_TOP + sum(heights) + 64
    c = _Canvas(DIGEST_W, height)
    c.text(
        32,
        44,
        f"Weekly test status · {d.since:%b %-d} – {d.until:%b %-d}",
        24,
        weight="bold",
    )
    c.text(
        32,
        72,
        "Each environment's latest runs this week, against the run before",
        14,
        INK_2,
    )
    c.legend(44, [cycles.REGRESSED, cycles.MIXED, cycles.IMPROVED, cycles.STEADY])
    col_w = (DIGEST_W - 32 - DIGEST_LABEL_W - DIGEST_FLAGS_W) / len(kinds)
    xs = [DIGEST_LABEL_W + i * col_w for i in range(len(kinds))]
    for x, kind in zip(xs, kinds):
        c.text(x, DIGEST_TOP - 16, cycles.KIND_LABELS[kind], 12, MUTED)
    c.text(DIGEST_W - 32 - DIGEST_FLAGS_W + 12, DIGEST_TOP - 16, "Flags", 12, MUTED)
    c.rule(DIGEST_TOP - 6)
    if not d.envs:
        c.text(32, DIGEST_TOP + 40, "No runs this week.", 15, INK_2)
    y0 = DIGEST_TOP
    for i, (env, h) in enumerate(zip(d.envs, heights)):
        if i % 2:
            c.band(y0, h)
        c.text(32, y0 + 34, (env.env or "no environment").upper(), 16, weight="bold")
        for x, kind in zip(xs, kinds):
            rows = env.cell(kind)
            if not rows:
                c.text(x, y0 + 34, "not run this week", 13, MUTED)
            for j, row in enumerate(rows):
                _entry(
                    c,
                    x,
                    y0 + 34 + j * DIGEST_ENTRY,
                    row,
                    perf_name=kind == cycles.PERFORMANCE,
                )
        n_flags = len(env.flags)
        c.text(
            DIGEST_W - 32 - DIGEST_FLAGS_W + 12,
            y0 + 34,
            f"{FLAG} {n_flags}" if n_flags else "–",
            15,
            INK if n_flags else MUTED,
            "bold" if n_flags else "normal",
        )
        y0 += h
        c.rule(y0)
    c.text(
        32,
        height - 22,
        "Full runs only. Regressions and fixes count agent results that changed "
        "between passing and not passing. Flags are listed in the message.",
        12,
        MUTED,
    )
    return c.png()
