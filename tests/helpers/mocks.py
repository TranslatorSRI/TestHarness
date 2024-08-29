from test_harness.reporter import Reporter
from test_harness.slacker import Slacker
from test_harness.runner.query_runner import QueryRunner


class MockReporter(Reporter):
    def __init__(self, base_url=None, refresh_token=None, logger=None):
        super().__init__()
        self.base_path = base_url
        self.test_run_id = 1
        pass

    async def get_auth(self):
        pass

    async def create_test_run(self, test_env, suite_name):
        return 1

    async def create_test(self, test, asset):
        return 2

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
        pass

    async def post_notification(self, messages=[]):
        print(f"posting messages: {messages}")
        pass

    async def upload_test_results_file(self, filename, extension, results):
        pass


class MockQueryRunner(QueryRunner):
    async def retrieve_registry(self, trapi_version: str):
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
