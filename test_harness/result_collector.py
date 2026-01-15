"""The Collector of Results."""

import logging
from typing import Dict, Optional, Union

from translator_testing_model.datamodel.pydanticmodel import (
    PathfinderTestAsset,
    PathfinderTestCase,
    TestAsset,
    TestCase,
    TestEnvEnum,
)

from test_harness.utils import AgentStatus, TestReport


def median_from_dict(total: int, count: dict[int, int]) -> int:
    """
    total is the number of requests made
    count is a dict {response_time: count}
    """
    pos = (total - 1) / 2
    k = 0
    for k in sorted(count.keys()):
        if pos < count[k]:
            return k
        pos -= count[k]

    return k


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
        ] if test_env != "dev" else [
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
        for result_stat in results_stats:
            self.performance_report["stats"][host_url] = {
                "endpoint": f"{host_url}{result_stat['name']}",
                "num_requests": result_stat.get("num_requests", 0)
                - result_stat.get("num_none_requests", 0),
                "num_failures": result_stat.get("num_failures", 0),
                "max_response_time": result_stat.get("max_response_time", -1),
                "min_response_time": result_stat.get("min_response_time", -1),
                "requests_per_second": result_stat.get("num_requests", 1)
                / (
                    result_stat.get("last_request_timestamp", -1)
                    - result_stat.get("start_time", 0)
                ),
                "average_response_time": result_stat.get("total_response_time")
                / (
                    result_stat.get("num_requests", 0)
                    - result_stat.get("num_none_requests", 0)
                ),
                "median_response_time": median_from_dict(
                    result_stat.get("num_requests", 0)
                    - result_stat.get("num_none_requests", 0),
                    result_stat.get("response_times", {}),
                ),
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
> Skipped: {self.acceptance_report['SKIPPED']}
> No Results: {self.acceptance_report['NO_RESULTS']}
> Errors: {self.acceptance_report['ERROR']}
"""
        if self.has_performance_results:
            results_formatted += """
> Performance Test Results:"""
            for target_url, target_stats in self.performance_report["stats"].items():
                results_formatted += f"""> {target_url}
> - Endpoint: {target_stats["endpoint"]}
> - Number of Requests: {target_stats["num_requests"]}
> - Number of Failures: {target_stats["num_failures"]}
> - Average Response Time: {target_stats["average_response_time"] / 1000} seconds
> - Median Response Time: {target_stats["median_response_time"] / 1000} seconds
> - Average Requests Per Second: {target_stats["requests_per_second"]}"""
            if len(self.performance_report["failures"].keys()):
                results_formatted += "> Failures:"
                for failure_stat in self.performance_report["failures"].values():
                    results_formatted += f"""---
> {failure_stat.get("name", "Unknown")}
> {failure_stat.get("error", "Unknown Error")}
> {failure_stat.get("occurrences", 0)}"""

        return results_formatted
