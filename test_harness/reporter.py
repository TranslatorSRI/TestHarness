"""Information Radiator Reporter."""
from datetime import datetime
import httpx
import logging
import os
from typing import List

from translator_testing_model.datamodel.pydanticmodel import TestCase, TestAsset


class Reporter:
    """Reports tests and statuses to the Information Radiator."""

    def __init__(
        self,
        base_url=None,
        refresh_token=None,
        logger: logging.Logger = logging.getLogger(),
    ):
        self.base_path = base_url if base_url else os.getenv("ZE_BASE_URL")
        self.refresh_token = (
            refresh_token if refresh_token else os.getenv("ZE_REFRESH_TOKEN")
        )
        self.authenticated_client = None
        self.test_run_id = None
        self.logger = logger

    async def get_auth(self):
        """Get access token for subsequent calls."""
        async with httpx.AsyncClient() as client:
            res = await client.post(
                url=f"{self.base_path}/api/iam/v1/auth/refresh",
                json={
                    "refreshToken": self.refresh_token,
                },
            )
            res.raise_for_status()
            auth_response = res.json()
        self.authenticated_client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {auth_response['authToken']}",
            }
        )

    async def create_test_run(self):
        """Create a test run in the IR."""
        res = await self.authenticated_client.post(
            url=f"{self.base_path}/api/reporting/v1/test-runs",
            json={
                "name": f"Test Harness Automated Tests: {datetime.now().strftime('%Y_%m_%d_%H_%M')}",
                "startedAt": datetime.now().astimezone().isoformat(),
                "framework": "Translator Automated Testing",
            },
        )
        res.raise_for_status()
        res_json = res.json()
        self.test_run_id = res_json["id"]
        return self.test_run_id

    async def create_test(self, test: TestCase, asset: TestAsset):
        """Create a test in the IR."""
        name = f"{asset.name if asset.name else asset.description}"
        res = await self.authenticated_client.post(
            url=f"{self.base_path}/api/reporting/v1/test-runs/{self.test_run_id}/tests",
            json={
                "name": name,
                "className": test.name,
                "methodName": asset.name,
                "startedAt": datetime.now().astimezone().isoformat(),
                "labels": [
                    {
                        "key": "TestCase",
                        "value": test.id,
                    },
                    {
                        "key": "TestAsset",
                        "value": asset.id,
                    },
                    {
                        "key": "Environment",
                        "value": test.test_env,
                    },
                ],
            },
        )
        res.raise_for_status()
        res_json = res.json()
        return res_json["id"]

    async def upload_labels(self, test_id: int, labels: List[dict]):
        """Upload labels to the IR."""
        self.logger.info(labels)
        res = await self.authenticated_client.put(
            url=f"{self.base_path}/api/reporting/v1/test-runs/{self.test_run_id}/tests/{test_id}/labels",
            json={
                "items": labels,
            },
        )
        res.raise_for_status()

    async def upload_logs(self, test_id: int, logs: List[str]):
        """Upload logs to the IR."""
        res = await self.authenticated_client.post(
            url=f"{self.base_path}/api/reporting/v1/test-runs/{self.test_run_id}/logs",
            json=[
                {
                    "testId": f"{test_id}",
                    "level": "INFO",
                    "timestamp": datetime.now().timestamp(),
                    "message": message,
                }
                for message in logs
            ],
        )
        res.raise_for_status()

    async def upload_artifact_references(self, test_id, artifact_references):
        """Upload artifact references to the IR."""
        res = await self.authenticated_client.put(
            url=f"{self.base_path}/api/reporting/v1/test-runs/{self.test_run_id}/tests/{test_id}/artifact-references",
            json=artifact_references,
        )
        res.raise_for_status()

    async def upload_screenshot(self, test_id, screenshot):
        """Upload screenshots to the IR."""
        res = await self.authenticated_client.post(
            url=f"{self.base_path}/api/reporting/v1/test-runs/{self.test_run_id}/tests/{test_id}/screenshots",
            headers={
                "Content-Type": "image/png",
            },
            data=screenshot,
        )
        res.raise_for_status()

    async def upload_log(self, test_id, message):
        """Upload logs to the IR."""
        res = await self.authenticated_client.post(
            url=f"{self.base_path}/api/reporting/v1/test-runs/{self.test_run_id}/logs",
            json=[
                {
                    "testId": f"{test_id}",
                    "level": "INFO",
                    "timestamp": datetime.now().timestamp(),
                    "message": message,
                },
            ],
        )
        res.raise_for_status()

    async def finish_test(self, test_id, result):
        """Set the final status of a test."""
        res = await self.authenticated_client.put(
            url=f"{self.base_path}/api/reporting/v1/test-runs/{self.test_run_id}/tests/{test_id}",
            json={
                "result": result,
                "endedAt": datetime.now().astimezone().isoformat(),
            },
        )
        res.raise_for_status()
        res_json = res.json()
        return res_json["result"]

    async def finish_test_run(self):
        """Set the final status of a test run."""
        res = await self.authenticated_client.put(
            url=f"{self.base_path}/api/reporting/v1/test-runs/{self.test_run_id}",
            json={
                "endedAt": datetime.now().astimezone().isoformat(),
            },
        )
        res.raise_for_status()
        res_json = res.json()
        return res_json["status"]
