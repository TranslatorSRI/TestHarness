from typing import Tuple
from test_harness.reporter import Reporter
from test_harness.slacker import Slacker
from translator_testing_model.datamodel.pydanticmodel import TestCase, TestAsset


class MockReporter(Reporter):
    def __init__(self, base_url=None, refresh_token=None, logger=None):
        Reporter.__init__(self, base_url, refresh_token, logger)
        self.base_path = base_url
        self.test_run_id = 1
        pass

    async def get_auth(self):
        pass

    async def create_test_run(self, test):
        return 1

    async def create_test(self, test, asset):
        return 2

    _mock_test_run_id: int = 0

    async def create_compliant_test(self, test_case_name: str, asset: TestAsset) -> Tuple[str, int]:
        test_case_id: str = f"{asset.id}-{test_case_name}"
        self._mock_test_run_id += 1
        return test_case_id, self._mock_test_run_id

    async def upload_labels(self, test_id, labels):
        pass

    async def upload_logs(self, test_id, logs):
        pass

    async def upload_artifact_references(self, test_id, artifact_references):
        pass

    async def upload_screenshots(self, test_id, screenshot):
        pass

    async def upload_log(self, test_id, message):
        pass

    async def finish_test(self, test_id, result):
        return result

    async def finish_test_run(self):
        pass


class MockSlacker(Slacker):
    def __init__(self):
        Slacker.__init__(self)
        pass

    async def post_notification(self, messages):
        print(f"posting messages: {messages}")
        pass
