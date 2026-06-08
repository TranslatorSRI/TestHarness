"""Information Radiator Reporter."""

import logging
import os
from datetime import datetime
from typing import List, Union

import httpx
from translator_testing_model.datamodel.pydanticmodel import (
    PathfinderTestAsset,
    PathfinderTestCase,
    PerformanceTestCase,
    TestAsset,
    TestCase,
)


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
        self.test_name = ""
        self.logger = logger

    def get_auth(self):
        """Get access token for subsequent calls."""
        with httpx.Client() as client:
            res = client.post(
                url=f"{self.base_path}/api/iam/v1/auth/refresh",
                json={
                    "refreshToken": self.refresh_token,
                },
            )
            res.raise_for_status()
            auth_response = res.json()
        self.authenticated_client = httpx.Client(
            headers={
                "Authorization": f"Bearer {auth_response['authToken']}",
            }
        )

    def create_test_run(self, test_env, suite_name):
        """Create a test run in the IR."""
        self.test_name = f"{suite_name}: {datetime.now().strftime('%Y_%m_%d_%H_%M')}"
        res = self.authenticated_client.post(
            url=f"{self.base_path}/api/reporting/v1/test-runs",
            json={
                "name": self.test_name,
                "startedAt": datetime.now().astimezone().isoformat(),
                "framework": "Translator Automated Testing",
                "config": {
                    "build": "v0.3.3",
                },
            },
        )
        res.raise_for_status()
        res_json = res.json()
        self.test_run_id = res_json["id"]
        return self.test_run_id

    def create_test(
        self,
        test: Union[TestCase, PathfinderTestCase, PerformanceTestCase],
        asset: Union[TestAsset, PathfinderTestAsset],
    ):
        """Create a test in the IR."""
        name = asset.name if asset.name else asset.description
        test_json = {
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
                    "key": "ExpectedOutput",
                    "value": asset.expected_output,
                },
            ],
        }
        if isinstance(test, PerformanceTestCase) and isinstance(asset, TestAsset):
            test_json["labels"].extend(
                [
                    {
                        "key": "InputCurie",
                        "value": asset.input_id,
                    },
                    {
                        "key": "TestRunTime",
                        "value": test.test_run_time,
                    },
                    {
                        "key": "UserSpawnRate",
                        "value": test.spawn_rate,
                    },
                ],
            )
        elif isinstance(test, PathfinderTestCase) and isinstance(
            asset, PathfinderTestAsset
        ):
            test_json["labels"].extend(
                [
                    {
                        "key": "SourceInputCurie",
                        "value": asset.source_input_id,
                    },
                    {
                        "key": "TargetInputCurie",
                        "value": asset.target_input_id,
                    },
                ]
            )
        elif isinstance(test, TestCase) and isinstance(asset, TestAsset):
            test_json["labels"].extend(
                [
                    {
                        "key": "InputCurie",
                        "value": asset.input_id,
                    },
                    {
                        "key": "OutputCurie",
                        "value": asset.output_id,
                    },
                ]
            )
        else:
            print("made it to the error section")
            raise Exception
        res = self.authenticated_client.post(
            url=f"{self.base_path}/api/reporting/v1/test-runs/{self.test_run_id}/tests",
            json=test_json,
        )
        res.raise_for_status()
        res_json = res.json()
        return res_json["id"]

    def upload_labels(self, test_id: int, labels: List[dict]):
        """Upload labels to the IR."""
        self.logger.info(labels)
        res = self.authenticated_client.put(
            url=f"{self.base_path}/api/reporting/v1/test-runs/{self.test_run_id}/tests/{test_id}/labels",
            json={
                "items": labels,
            },
        )
        res.raise_for_status()

    def upload_logs(self, test_id: int, logs: List[str]):
        """Upload logs to the IR."""
        res = self.authenticated_client.post(
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

    def upload_artifact_references(self, test_id, artifact_references):
        """Upload artifact references to the IR."""
        res = self.authenticated_client.put(
            url=f"{self.base_path}/api/reporting/v1/test-runs/{self.test_run_id}/tests/{test_id}/artifact-references",
            json=artifact_references,
        )
        res.raise_for_status()

    def upload_screenshot(self, test_id, screenshot):
        """Upload screenshots to the IR."""
        res = self.authenticated_client.post(
            url=f"{self.base_path}/api/reporting/v1/test-runs/{self.test_run_id}/tests/{test_id}/screenshots",
            headers={
                "Content-Type": "image/png",
            },
            data=screenshot,
            timeout=30,
        )
        res.raise_for_status()

    def upload_log(self, test_id, message):
        """Upload logs to the IR."""
        try:
            res = self.authenticated_client.post(
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
        except httpx.HTTPStatusError as e:
            self.logger.error(f"[{test_id}] failed to upload logs: {e}")

    def finish_test(self, test_id: str, result: str):
        """Set the final status of a test."""
        try:
            res = self.authenticated_client.put(
                url=f"{self.base_path}/api/reporting/v1/test-runs/{self.test_run_id}/tests/{test_id}",
                json={
                    "result": result,
                    "endedAt": datetime.now().astimezone().isoformat(),
                },
            )
            res.raise_for_status()
        except httpx.HTTPStatusError as e:
            self.logger.error(f"[{test_id}] failed to upload finished status: {e}")

    def finish_test_run(self):
        """Set the final status of a test run."""
        res = self.authenticated_client.put(
            url=f"{self.base_path}/api/reporting/v1/test-runs/{self.test_run_id}",
            json={
                "endedAt": datetime.now().astimezone().isoformat(),
            },
        )
        res.raise_for_status()
        res_json = res.json()
        return res_json["status"]
