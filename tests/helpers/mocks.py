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
import logging
import os
from datetime import datetime
from typing import List, Union
from translator_testing_model.datamodel.pydanticmodel import (
    PathfinderTestAsset,
    PathfinderTestCase,
    PerformanceTestCase,
    TestAsset,
    TestCase,
)



class MockReporter:
    """
    Mock implementation of Reporter.

    This class has the same public interface as Reporter, but instead of
    performing any network I/O it only logs the parameters passed to each
    method and returns simple dummy values where needed.
    """

    def __init__(
        self,
        base_url=None,
        refresh_token=None,
        logger: logging.Logger = logging.getLogger(),
    ):
        self.base_path = base_url
        self.refresh_token = refresh_token
        self.authenticated_client = None
        self.test_run_id = None
        self.test_name = ""
        self.logger = logger.getChild("MockReporter")

        self.logger.info(
            "Initialized MockReporter with base_url=%r, refresh_token=%r",
            base_url,
            refresh_token,
        )

    def get_auth(self):
        """Mock get_auth: just log and do nothing."""
        self.logger.info(
            "Mock get_auth called with base_path=%r, refresh_token=%r",
            self.base_path,
            self.refresh_token,
        )
        # No-op: do not perform any HTTP calls.

    def create_test_run(self, test_env, suite_name):
        """Mock create_test_run: log parameters and return a dummy test_run_id."""
        self.logger.info(
            "Mock create_test_run called with test_env=%r, suite_name=%r",
            test_env,
            suite_name,
        )
        self.test_name = f"{suite_name}: {datetime.now().strftime('%Y_%m_%d_%H_%M')}"
        # Set a simple dummy ID so callers that expect a value don't break.
        self.test_run_id = "mock-test-run-id"
        self.logger.debug(
            "Mock test run created with test_name=%r, test_run_id=%r",
            self.test_name,
            self.test_run_id,
        )
        return 1

    def create_test(
        self,
        test: Union[TestCase, PathfinderTestCase, PerformanceTestCase],
        asset: Union[TestAsset, PathfinderTestAsset],
    ):
        """Mock create_test: log parameters and return a dummy test_id."""
        self.logger.info(
            "Mock create_test called with test=%r, asset=%r, test_run_id=%r",
            test,
            asset,
            self.test_run_id,
        )
        dummy_test_id = "mock-test-id"
        self.logger.debug("Mock test created with id=%r", dummy_test_id)
        return 2

    def upload_labels(self, test_id: int, labels: List[dict]):
        """Mock upload_labels: log parameters and do nothing."""
        self.logger.info(
            "Mock upload_labels called with test_id=%r, labels=%r",
            test_id,
            labels,
        )

    def upload_logs(self, test_id: int, logs: List[str]):
        """Mock upload_logs: log parameters and do nothing."""
        self.logger.info(
            "Mock upload_logs called with test_id=%r, logs=%r",
            test_id,
            logs,
        )

    def upload_artifact_references(self, test_id, artifact_references):
        """Mock upload_artifact_references: log parameters and do nothing."""
        self.logger.info(
            "Mock upload_artifact_references called with test_id=%r, artifact_references=%r",
            test_id,
            artifact_references,
        )

    def upload_screenshot(self, test_id, screenshot):
        """Mock upload_screenshot: log parameters and do nothing."""
        self.logger.info(
            "Mock upload_screenshot called with test_id=%r, screenshot_length=%r",
            test_id,
            len(screenshot) if screenshot is not None else None,
        )

    def upload_log(self, test_id, message):
        """Mock upload_log: log parameters and do nothing."""
        self.logger.info(
            "Mock upload_log called with test_id=%r, message=%r",
            test_id,
            message,
        )

    def finish_test(self, test_id, result):
        """Mock finish_test: log parameters and return the given result."""
        self.logger.info(
            "Mock finish_test called with test_id=%r, result=%r",
            test_id,
            result,
        )
        return result

    def finish_test_run(self):
        """Mock finish_test_run: log and return a dummy status."""
        self.logger.info(
            "Mock finish_test_run called with test_run_id=%r",
            self.test_run_id,
        )
        dummy_status = "MOCK_COMPLETED"
        self.logger.debug("Mock test run finished with status=%r", dummy_status)
        return dummy_status

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
                "ara": [
                    {
                        "_id": "ARAX Translator Reasoner - TRAPI 1.6.0",
                        "title": "ARAX Translator Reasoner - TRAPI 1.6.0",
                        "infores": "infores:arax",
                        # "url": "http://localhost:5439/arax"
                        "url": "http://localhost:5000/api/arax/v1.4"
                    }
                ]
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
