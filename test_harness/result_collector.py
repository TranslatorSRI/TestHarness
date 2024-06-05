"""The Collector of Results."""
from translator_testing_model.datamodel.pydanticmodel import TestAsset, TestCase

from .utils import get_tag


class ResultCollector:
    """Collect results for easy dissemination."""

    def __init__(self):
        """Initialize the Collector."""
        self.agents = ["ars", "aragorn", "arax", "bte", "improving", "unsecret", "cqs"]
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
    
    def collect_result(self, test: TestCase, asset: TestAsset, result: dict, url: str):
        """Add a single result to the total output."""
        # add result to stats
        for agent in result["result"]:
            query_type = asset.expected_output
            result_type = self.result_types.get(get_tag(result["result"][agent]), "Test Error")
            self.stats[agent][query_type][result_type] += 1
        
        # add result to csv
        agent_results = ",".join(get_tag(result["result"][agent]) for agent in self.agents)
        ars_pk = result["pks"].get("parent_pk", None)
        pk_url = f"https://arax.ncats.io/?r={ars_pk}" if ars_pk is not None else ""
        self.csv += f"""{asset.name},{url},{pk_url},{test.id},{asset.id},{agent_results}\n"""
