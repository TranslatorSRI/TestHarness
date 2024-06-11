"""Slack notification integration class."""

import httpx
import json
import os
from slack_sdk import WebClient
import tempfile


class Slacker:
    """Slack notification poster."""

    def __init__(self, url=None, token=None, slack_channel=None):
        self.channel = (
            slack_channel if slack_channel is not None else os.getenv("SLACK_CHANNEL")
        )
        self.url = url if url is not None else os.getenv("SLACK_WEBHOOK_URL")
        slack_token = token if token is not None else os.getenv("SLACK_TOKEN")
        self.client = WebClient(slack_token)

    async def post_notification(self, messages=[]):
        """Post a notification to Slack."""
        # https://gist.github.com/mrjk/079b745c4a8a118df756b127d6499aa0
        blocks = []
        for message in messages:
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": str(message),
                    },
                }
            )
        async with httpx.AsyncClient() as client:
            res = await client.post(
                url=self.url,
                json={
                    "text": ", ".join(block["text"]["text"] for block in blocks),
                    "blocks": blocks,
                },
            )

    async def upload_test_results_file(self, filename, extension, results):
        """Upload a results file to Slack."""
        with tempfile.TemporaryDirectory() as td:
            tmp_path = os.path.join(td, f"{filename}.{extension}")
            with open(tmp_path, "w") as f:
                if extension == "csv":
                    f.write(results)
                elif extension == "json":
                    json.dump(results, f, indent=2)
            self.client.files_upload_v2(
                channel=self.channel,
                title=filename,
                file=tmp_path,
                initial_comment="Test Results:",
            )
