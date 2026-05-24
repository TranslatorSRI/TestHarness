"""Render Locust-style time-series charts from a stats_history snapshot."""

from __future__ import annotations

import io
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt


logger = logging.getLogger(__name__)


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if isinstance(value, (int, float)):
        return datetime.utcfromtimestamp(value)
    if isinstance(value, str):
        # Locust uses ISO 8601 with a trailing 'Z' for UTC.
        text = value.rstrip("Z")
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None
    return None


def _series(
    history: Sequence[Dict[str, Any]], key: str
) -> Tuple[List[datetime], List[float]]:
    """Pull a (times, values) pair out of one stats_history series.

    Each entry's value is a ``[timestamp, value]`` pair, but we prefer the
    snapshot-level ``time`` so all series share the same X axis.
    """
    times: List[datetime] = []
    values: List[float] = []
    for row in history:
        ts = _parse_timestamp(row.get("time"))
        entry = row.get(key)
        if ts is None or not isinstance(entry, (list, tuple)) or len(entry) < 2:
            continue
        value = entry[1]
        if value is None:
            continue
        times.append(ts)
        values.append(float(value))
    return times, values


def _percentile_keys(history: Sequence[Dict[str, Any]]) -> List[str]:
    """Return percentile series keys present in the snapshots, sorted."""
    keys = set()
    for row in history:
        for key in row.keys():
            if key.startswith("response_time_percentile_"):
                keys.add(key)
    return sorted(keys, key=lambda k: float(k.split("_")[-1]))


def render_history_png(history: Sequence[Dict[str, Any]], title: str) -> bytes:
    """Render a Locust-style three-panel chart for the given history.

    Panels (top to bottom):
      1. Total RPS, with failures/sec overlaid.
      2. Response time percentiles in milliseconds.
      3. Active user count.

    Returns the PNG bytes. Caller is responsible for handling empty history;
    we raise if there isn't enough data to plot.
    """
    if len(history) < 2:
        raise ValueError("history too short to plot")

    fig, (ax_rps, ax_rt, ax_users) = plt.subplots(
        nrows=3, ncols=1, figsize=(10, 12), sharex=True
    )

    rps_times, rps_values = _series(history, "current_rps")
    fail_times, fail_values = _series(history, "current_fail_per_sec")
    ax_rps.plot(rps_times, rps_values, color="#2ca02c", label="RPS")
    ax_rps.plot(fail_times, fail_values, color="#d62728", label="Failures/s")
    ax_rps.set_ylabel("Requests / s")
    ax_rps.set_title("Total Requests per Second")
    ax_rps.grid(True, alpha=0.3)
    ax_rps.legend(loc="upper right")

    percentile_colors = ["#1f77b4", "#ff7f0e", "#9467bd", "#8c564b"]
    for color, key in zip(percentile_colors, _percentile_keys(history)):
        times, values = _series(history, key)
        label = "p" + key.split("_")[-1].replace("0.", "")
        ax_rt.plot(times, values, color=color, label=label)
    ax_rt.set_ylabel("Milliseconds")
    ax_rt.set_title("Response Times")
    ax_rt.grid(True, alpha=0.3)
    ax_rt.legend(loc="upper right")

    user_times, user_values = _series(history, "user_count")
    ax_users.step(user_times, user_values, color="#1f77b4", where="post")
    ax_users.set_ylabel("Users")
    ax_users.set_title("Number of Users")
    ax_users.set_xlabel("Time (UTC)")
    ax_users.grid(True, alpha=0.3)

    ax_users.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
    fig.autofmt_xdate()
    fig.suptitle(title)
    fig.tight_layout(rect=(0, 0, 1, 0.97))

    buf = io.BytesIO()
    try:
        fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    finally:
        plt.close(fig)
    return buf.getvalue()
