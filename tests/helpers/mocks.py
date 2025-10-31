from typing import Dict
from translator_testing_model.datamodel.pydanticmodel import (
    PathfinderTestAsset,
    PathfinderTestCase,
    TestAsset,
    TestCase,
)
from test_harness.reporter import Reporter
from test_harness.result_collector import ResultCollector
from test_harness.slacker import Slacker
from test_harness.runner.query_runner import QueryRunner


class MockReporter(Reporter):
    def __init__(self, base_url=None, refresh_token=None, logger=None):
        super().__init__()
        self.base_path = base_url
        self.test_run_id = 1
        pass

    def get_auth(self):
        pass

    def create_test_run(self, test_env, suite_name):
        return 1

    def create_test(self, test, asset):
        return 2

    def upload_labels(self, test_id, labels):
        pass

    def upload_logs(self, test_id, logs):
        pass

    def upload_artifact_references(self, test_id, artifact_references):
        pass

    def upload_screenshots(self, test_id, screenshot):
        pass

    def upload_log(self, test_id, message):
        pass

    def finish_test(self, test_id, result):
        return result

    def finish_test_run(self):
        pass


class MockSlacker(Slacker):
    def __init__(self):
        pass

    def post_notification(self, messages=[]):
        print(f"posting messages: {messages}")
        pass

    def upload_test_results_file(self, filename, extension, results):
        pass


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
    ):
        return super().collect_acceptance_result(test, asset, report, parent_pk, url)

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
