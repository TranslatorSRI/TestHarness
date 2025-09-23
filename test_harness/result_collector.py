"""The Collector of Results."""

import logging
from typing import Union
from translator_testing_model.datamodel.pydanticmodel import TestAsset, PathfinderTestAsset, TestCase, PathfinderTestCase

from test_harness.utils import get_tag


class ResultCollector:
    """Collect results for easy dissemination."""

    def __init__(self, logger: logging.Logger):
        """Initialize the Collector."""
        self.logger = logger
        self.agents = [
            "ars",
            "aragorn",
            "arax",
            "biothings-explorer",
            "improving-agent",
            "unsecret-agent",
            "cqs",
        ]
        self.query_types = ["TopAnswer", "Acceptable", "BadButForgivable", "NeverShow"]
        self.result_types = {
            "PASSED": "PASSED",
            "FAILED": "FAILED",
            "No results": "No results",
            "-": "Test Error",
        }
        self.stats = {}
        for agent in self.agents:
            self.stats[agent] = {}
            for query_type in self.query_types:
                self.stats[agent][query_type] = {}
                for result_type in self.result_types.values():
                    self.stats[agent][query_type][result_type] = 0

        self.columns = ["name", "url", "pk", "TestCase", "TestAsset", *self.agents]
        header = ",".join(self.columns)
        self.csv = f"{header}\n"

    def collect_result(
        self,
        test: Union[TestCase, PathfinderTestCase],
        asset: Union[TestAsset, PathfinderTestAsset],
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
                if (
                    query_type in self.stats[agent]
                    and result_type in self.stats[agent][query_type]
                ):
                    self.stats[agent][query_type][result_type] += 1
                else:
                    self.logger.error(
                        f"Got {query_type} and {result_type} and can't put into stats!"
                    )

        # add result to csv
        agent_results = ",".join(
            get_tag(report.get(agent, {"status": "Not queried"}))
            for agent in self.agents
        )
        pk_url = (
            f"https://arax.ci.ncats.io/?r={parent_pk}" if parent_pk is not None else ""
        )
        self.csv += (
            f""""{asset.name}",{url},{pk_url},{test.id},{asset.id},{agent_results}\n"""
        )
