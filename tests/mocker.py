from test_harness.reporter import Reporter
from test_harness.slacker import Slacker

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
