"""The Collector of Results."""

import logging
from typing import Dict, Iterable, List, Optional, Union

from translator_testing_model.datamodel.pydanticmodel import (
    PathfinderTestAsset,
    PathfinderTestCase,
    TestAsset,
    TestCase,
    TestEnvEnum,
)

from test_harness.utils import AgentStatus, TestReport


# Stat row identifiers produced by the performance test runner. Kept in sync
# with the constants in performance_test_runner.py.
QUERY_TYPE = "QUERY"
SUBMIT_NAME = "submit_query"
POLL_NAME = "poll_status"

ARS_OUTCOMES = (
    "ars_query_completed",
    "ars_query_errored",
    "ars_query_polling_failed",
    "ars_query_timed_out",
    "ars_query_abandoned",
)
ARA_OUTCOMES = (
    "ara_query_completed",
    "ara_query_failed",
)


def percentile_from_dict(total: int, counts: Dict[int, int], pct: float) -> int:
    """Return the response_time bucket at a given percentile (0..1)."""
    if total <= 0 or not counts:
        return 0
    target = max(0.0, min(1.0, pct)) * (total - 1)
    cumulative = 0
    last_bucket = 0
    for bucket in sorted(counts.keys()):
        cumulative += counts[bucket]
        last_bucket = bucket
        if cumulative > target:
            return bucket
    return last_bucket


def median_from_dict(total: int, count: Dict[int, int]) -> int:
    """Backwards-compatible median helper."""
    return percentile_from_dict(total, count, 0.5)


def _safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _summarize_layer(stat: Optional[Dict]) -> Dict:
    """Pull the metrics we care about out of a single Locust stats row."""
    if not stat:
        return {
            "num_requests": 0,
            "num_failures": 0,
            "min_response_time": 0,
            "max_response_time": 0,
            "avg_response_time": 0.0,
            "median_response_time": 0,
            "p95_response_time": 0,
            "requests_per_second": 0.0,
        }
    num_requests = stat.get("num_requests", 0)
    num_none = stat.get("num_none_requests", 0)
    measured = max(0, num_requests - num_none)
    response_times = stat.get("response_times", {}) or {}
    duration = (
        stat.get("last_request_timestamp", 0) or 0
    ) - (stat.get("start_time", 0) or 0)
    return {
        "num_requests": num_requests,
        "num_failures": stat.get("num_failures", 0),
        "min_response_time": stat.get("min_response_time") or 0,
        "max_response_time": stat.get("max_response_time", 0),
        "avg_response_time": _safe_div(
            stat.get("total_response_time", 0), measured
        ),
        "median_response_time": percentile_from_dict(measured, response_times, 0.5),
        "p95_response_time": percentile_from_dict(measured, response_times, 0.95),
        "requests_per_second": _safe_div(num_requests, duration),
    }


def _find_stat(
    stats: Iterable[Dict], name: str, method: Optional[str] = None
) -> Optional[Dict]:
    for stat in stats:
        if stat.get("name") != name:
            continue
            ##
        if method is not None and stat.get("method") != method:
            continue
        return stat
    return None


def _summarize_query_lifecycle(
    stats: List[Dict], outcome_names: Iterable[str]
) -> Dict:
    """Aggregate end-to-end QUERY events across all outcomes."""
    total_requests = 0
    total_response_time = 0.0
    min_rt: Optional[int] = None
    max_rt = 0
    combined_buckets: Dict[int, int] = {}
    by_outcome: Dict[str, int] = {name: 0 for name in outcome_names}

    for stat in stats:
        if stat.get("method") != QUERY_TYPE:
            continue
        name = stat.get("name", "")
        count = stat.get("num_requests", 0)
        if name in by_outcome:
            by_outcome[name] = count
        total_requests += count
        total_response_time += stat.get("total_response_time", 0) or 0
        stat_min = stat.get("min_response_time")
        if stat_min is not None and (min_rt is None or stat_min < min_rt):
            min_rt = stat_min
        stat_max = stat.get("max_response_time", 0) or 0
        if stat_max > max_rt:
            max_rt = stat_max
        for bucket, n in (stat.get("response_times") or {}).items():
            combined_buckets[bucket] = combined_buckets.get(bucket, 0) + n

    completed_name = next(
        (name for name in outcome_names if name.endswith("_completed")), None
    )
    completed_stat = _find_stat(stats, completed_name, QUERY_TYPE) if completed_name else None
    completed_buckets = (completed_stat or {}).get("response_times", {}) or {}
    completed_count = (completed_stat or {}).get("num_requests", 0)
    completed_total_rt = (completed_stat or {}).get("total_response_time", 0) or 0

    return {
        "total_queries": total_requests,
        "by_outcome": by_outcome,
        "all_outcomes": {
            "min_response_time": min_rt or 0,
            "max_response_time": max_rt,
            "avg_response_time": _safe_div(total_response_time, total_requests),
            "median_response_time": percentile_from_dict(
                total_requests, combined_buckets, 0.5
            ),
            "p95_response_time": percentile_from_dict(
                total_requests, combined_buckets, 0.95
            ),
        },
        "completed_only": {
            "count": completed_count,
            "avg_response_time": _safe_div(completed_total_rt, completed_count),
            "median_response_time": percentile_from_dict(
                completed_count, completed_buckets, 0.5
            ),
            "p95_response_time": percentile_from_dict(
                completed_count, completed_buckets, 0.95
            ),
            "min_response_time": (completed_stat or {}).get("min_response_time") or 0,
            "max_response_time": (completed_stat or {}).get("max_response_time", 0) or 0,
        },
    }


class ResultCollector:
    """Collect results for easy dissemination."""

    def __init__(self, test_env: Optional[TestEnvEnum], logger: logging.Logger):
        """Initialize the Collector."""
        self.logger = logger
        self.has_acceptance_results = False
        self.has_performance_results = False
        agents = [
            "ars",
            "aragorn",
            "arax",
            "biothings-explorer",
            "improving-agent",
            "unsecret-agent",
            "cqs",
        ]
        if test_env == "dev" or test_env == "ci":
            agents = [
                "ars",
                "shepherd-aragorn",
                "shepherd-arax",
                "shepherd-bte",
            ]
        self.agents = agents
        self.query_types = ["TopAnswer", "Acceptable", "BadButForgivable", "NeverShow"]
        self.acceptance_report = {
            status_type.value: 0
            for status_type in AgentStatus
        }
        self.acceptance_stats = {}
        for agent in self.agents:
            self.acceptance_stats[agent] = {}
            for query_type in self.query_types:
                self.acceptance_stats[agent][query_type] = {}
                for result_type in self.acceptance_report.keys():
                    self.acceptance_stats[agent][query_type][result_type] = 0

        self.columns = ["name", "url", "pk", "TestCase", "TestAsset", *self.agents]
        header = ",".join(self.columns)
        self.acceptance_csv = f"{header}\n"
        self.performance_stats = {}
        self.performance_report = {
            "stats": {},
            "failures": {},
        }

    def collect_acceptance_result(
        self,
        test: Union[TestCase, PathfinderTestCase],
        asset: Union[TestAsset, PathfinderTestAsset],
        report: TestReport,
        parent_pk: Union[str, None],
        url: str,
    ):
        """Add a single report to the total output."""
        self.has_acceptance_results = True
        # add result to stats
        agent_statuses = []
        for agent in self.agents:
            query_type = asset.expected_output
            if agent in report.result:
                agent_result = report.result[agent]
                self.acceptance_stats[agent][query_type][agent_result.status.value] += 1
                agent_statuses.append(agent_result.status.value)
            else:
                agent_statuses.append(AgentStatus.SKIPPED.value)

        # add result to csv
        agent_results = ",".join(agent_statuses)
        pk_url = (
            f"https://arax.ci.transltr.io/?r={parent_pk}"
            if parent_pk is not None else ""
        )
        self.acceptance_csv += (
            f""""{asset.name}",{url},{pk_url},{test.id},{asset.id},{agent_results}\n"""
        )

    def collect_performance_result(
        self,
        test: Union[TestCase, PathfinderTestCase],
        asset: Union[TestAsset, PathfinderTestAsset],
        url: str,
        host_url: str,
        results: Dict,
    ):
        """Add a single report for a performance test."""
        self.has_performance_results = True
        results_stats = results.get("stats") or []
        target = (results.get("target") or "").lower()
        outcome_names = ARS_OUTCOMES if target == "ars" else ARA_OUTCOMES

        submit_stat = _find_stat(results_stats, SUBMIT_NAME)
        poll_stat = _find_stat(results_stats, POLL_NAME) if target == "ars" else None
        lifecycle = _summarize_query_lifecycle(results_stats, outcome_names)

        self.performance_report["stats"][host_url] = {
            "target": target,
            "test_run_time": results.get("test_run_time"),
            "spawn_rate": results.get("spawn_rate"),
            "submit": _summarize_layer(submit_stat),
            "poll": _summarize_layer(poll_stat) if target == "ars" else None,
            "queries": lifecycle,
        }
        self.performance_report["failures"] = results.get("failures") or {}

        stats_id = f"{host_url}_case_{test.id}_asset_{asset.id}"
        self.performance_stats[stats_id] = {
            "information_radiator_url": url,
            **results,
        }

    def dump_result_summary(self):
        """Format test results summary for Slack."""
        results_formatted = ""
        if self.has_acceptance_results:
            results_formatted += f"""
> Acceptance Test Results:
> Passed: {self.acceptance_report['PASSED']},
> Failed: {self.acceptance_report['FAILED']},
> Regression: {self.acceptance_report['REGRESSION']},
> Skipped: {self.acceptance_report['SKIPPED']}
> No Results: {self.acceptance_report['NO_RESULTS']}
> Errors: {self.acceptance_report['ERROR']}
"""
        if self.has_performance_results:
            results_formatted += """
> Performance Test Results:"""
            for target_url, target_stats in self.performance_report["stats"].items():
                results_formatted += self._format_performance_target(
                    target_url, target_stats
                )
            if len(self.performance_report["failures"].keys()):
                results_formatted += "\n> Failures:"
                for failure_stat in self.performance_report["failures"].values():
                    results_formatted += (
                        f"\n> ---"
                        f"\n> {failure_stat.get('name', 'Unknown')}"
                        f"\n> {failure_stat.get('error', 'Unknown Error')}"
                        f"\n> occurrences: {failure_stat.get('occurrences', 0)}"
                    )

        return results_formatted

    @staticmethod
    def _format_performance_target(target_url: str, target_stats: Dict) -> str:
        """Render the per-host performance section of the summary."""
        ms_to_s = lambda ms: ms / 1000.0  # noqa: E731

        lines = [f"> {target_url}"]
        target = target_stats.get("target") or "unknown"
        run_time = target_stats.get("test_run_time")
        spawn_rate = target_stats.get("spawn_rate")
        if run_time is not None or spawn_rate is not None:
            lines.append(
                f"> - Target: {target} | run_time={run_time}s, spawn_rate={spawn_rate}"
            )

        queries = target_stats.get("queries") or {}
        total_queries = queries.get("total_queries", 0)
        by_outcome = queries.get("by_outcome", {}) or {}
        completed = queries.get("completed_only") or {}
        all_outcomes = queries.get("all_outcomes") or {}
        run_time_seconds = run_time or 0

        lines.append("> - End-to-end queries:")
        lines.append(f">   * Submitted (recorded): {total_queries}")
        for name, count in by_outcome.items():
            label = name.replace("ars_query_", "").replace("ara_query_", "")
            lines.append(f">   * {label}: {count}")
        completed_count = completed.get("count", 0)
        if run_time_seconds:
            throughput = completed_count / (run_time_seconds / 60.0)
            lines.append(
                f">   * Completed throughput: {throughput:.2f} queries/minute"
            )
        if completed_count:
            lines.append(
                ">   * Completed query time (s): "
                f"avg={ms_to_s(completed['avg_response_time']):.1f}, "
                f"median={ms_to_s(completed['median_response_time']):.1f}, "
                f"p95={ms_to_s(completed['p95_response_time']):.1f}, "
                f"min={ms_to_s(completed['min_response_time']):.1f}, "
                f"max={ms_to_s(completed['max_response_time']):.1f}"
            )
        if total_queries:
            lines.append(
                ">   * All-outcome query time (s): "
                f"avg={ms_to_s(all_outcomes['avg_response_time']):.1f}, "
                f"median={ms_to_s(all_outcomes['median_response_time']):.1f}, "
                f"p95={ms_to_s(all_outcomes['p95_response_time']):.1f}, "
                f"min={ms_to_s(all_outcomes['min_response_time']):.1f}, "
                f"max={ms_to_s(all_outcomes['max_response_time']):.1f}"
            )

        submit = target_stats.get("submit") or {}
        if submit.get("num_requests"):
            lines.append(
                "> - Submit requests: "
                f"count={submit['num_requests']}, "
                f"failures={submit['num_failures']}, "
                f"avg={ms_to_s(submit['avg_response_time']):.2f}s, "
                f"median={ms_to_s(submit['median_response_time']):.2f}s, "
                f"p95={ms_to_s(submit['p95_response_time']):.2f}s, "
                f"rps={submit['requests_per_second']:.3f}"
            )

        poll = target_stats.get("poll")
        if poll and poll.get("num_requests"):
            lines.append(
                "> - Poll requests: "
                f"count={poll['num_requests']}, "
                f"failures={poll['num_failures']}, "
                f"avg={ms_to_s(poll['avg_response_time']):.2f}s, "
                f"median={ms_to_s(poll['median_response_time']):.2f}s, "
                f"p95={ms_to_s(poll['p95_response_time']):.2f}s, "
                f"rps={poll['requests_per_second']:.3f}"
            )

        return "\n" + "\n".join(lines)
