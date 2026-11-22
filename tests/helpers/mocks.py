from typing import Dict
from translator_testing_model.datamodel.pydanticmodel import (
    PathfinderTestAsset,
    PathfinderTestCase,
    TestAsset,
    TestCase,
)
from test_harness.result_collector import ResultCollector
from test_harness.slacker import Slacker
from test_harness.runner.query_runner import QueryRunner


class MockSlacker(Slacker):
    def __init__(self):
        pass

    def post_notification(self, messages=[]):
        print(f"posting messages: {messages}")
        pass

    def upload_test_results_file(self, filename, extension, results):
        pass

    def post_thread(self, summary, image=None, details=(), files=()):
        print(f"posting a thread: {summary}")


class MockQueryRunner(QueryRunner):
    def retrieve_registry(self, trapi_version: str):
        self.registry = {
            "staging": {
                "ars": [
                    {
                        "_id": "testing",
                        "title": "Tester",
                        "infores": "infores:tester",
                        "url": "http://tester",
                    }
                ],
            },
        }


class MockResultCollector(ResultCollector):
    def collect_acceptance_result(
        self,
        test: TestCase | PathfinderTestCase,
        asset: TestAsset | PathfinderTestAsset,
        report: dict,
        parent_pk: str | None,
        url: str,
        force_skipped: bool = False,
        status=None,
        message=None,
    ):
        return super().collect_acceptance_result(
            test,
            asset,
            report,
            parent_pk,
            url,
            force_skipped=force_skipped,
            status=status,
            message=message,
        )

    def collect_performance_result(
        self,
        test: TestCase | PathfinderTestCase,
        asset: TestAsset | PathfinderTestAsset,
        url: str,
        host_url: str,
        results: Dict,
    ):
        return super().collect_performance_result(test, asset, url, host_url, results)

    def dump_result_summary(self):
        return super().dump_result_summary()
