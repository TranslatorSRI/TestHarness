"""Information Radiator Reporter."""
from datetime import datetime
import httpx
import json
import os
from pathlib import Path

from .models import TestCase


class Reporter():
    """Reports tests and statuses to the Information Radiator."""

    def __init__(self, base_url = None, refresh_token = None):
        self.base_path = base_url if base_url else os.getenv("ZE_BASE_URL")
        self.refresh_token = refresh_token if refresh_token else os.getenv("ZE_REFRESH_TOKEN")
        self.authenticated_client = None
        self.test_run_id = None
    
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
                "name": "Test",
                "startedAt": datetime.now().astimezone().isoformat(),
                "framework": "Translator Automated Testing",
            },
        )
        res.raise_for_status()
        res_json = res.json()
        self.test_run_id = res_json["id"]
        return self.test_run_id
    
    async def create_test(self, test: TestCase):
        """Create a test in the IR."""
        res = await self.authenticated_client.post(
            url=f"{self.base_path}/api/reporting/v1/test-runs/{self.test_run_id}/tests",
            json={
                "name": test.type,
                "className": test.type,
                "methodName": test.type,
                "startedAt": datetime.now().astimezone().isoformat(),
            },
        )
        res.raise_for_status()
        res_json = res.json()
        return res_json["id"]
    
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
        