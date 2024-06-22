"""The Collector of Results."""

from typing import Union
from translator_testing_model.datamodel.pydanticmodel import TestAsset, TestCase

from test_harness.utils import get_tag


class ResultCollector:
    """Collect results for easy dissemination."""

    def __init__(self):
        """Initialize the Collector."""
        self.agents = [
            "ars",
            "aragorn",
            "arax",
            "biothings-explorer",
            "improving-agent",
            "unsecret-agent",
            "cqs",
        ]
        query_types = ["TopAnswer", "Acceptable", "BadButForgivable", "NeverShow"]
        self.result_types = {
            "PASSED": "PASSED",
            "FAILED": "FAILED",
            "No results": "No results",
            "-": "Test Error",
        }
        self.stats = {}
        for agent in self.agents:
            self.stats[agent] = {}
            for query_type in query_types:
                self.stats[agent][query_type] = {}
                for result_type in self.result_types.values():
                    self.stats[agent][query_type][result_type] = 0

        self.columns = ["name", "url", "pk", "TestCase", "TestAsset", *self.agents]
        header = ",".join(self.columns)
        self.csv = f"{header}\n"

    def collect_result(
        self,
        test: TestCase,
        asset: TestAsset,
        report: dict,
        parent_pk: Union[str, None],
        url: str,
    ):
        """Add a single report to the total output."""
        # add result to stats
        for agent in self.agents:
            query_type = asset.expected_output
            if agent in report:
                result_type = self.result_types.get(
                    get_tag(report[agent]), "Test Error"
                )
                self.stats[agent][query_type][result_type] += 1

        # add result to csv
        agent_results = ",".join(
            get_tag(report[agent]) for agent in self.agents if agent in report
        )
        pk_url = (
            f"https://arax.ncats.io/?r={parent_pk}" if parent_pk is not None else ""
        )
        self.csv += (
            f"""{asset.name},{url},{pk_url},{test.id},{asset.id},{agent_results}\n"""
        )