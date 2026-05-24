"""Slack notification integration class."""

import json
import logging
import os
import tempfile

import httpx
from slack_sdk import WebClient


# Slack rejects section blocks whose text exceeds 3000 chars. Leave a small
# safety margin so we never end up at the boundary.
SLACK_SECTION_TEXT_LIMIT = 2900


def _chunk_text(text, limit=SLACK_SECTION_TEXT_LIMIT):
    """Split text into chunks no larger than ``limit`` chars, preferring
    newline boundaries so quoted-block formatting stays intact."""
    if len(text) <= limit:
        return [text]
    chunks = []
    current = ""
    for line in text.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        # A single line longer than the limit (rare) - hard split it.
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        current = line
    if current:
        chunks.append(current)
    return chunks


class Slacker:
    """Slack notification poster."""

    def __init__(self, url=None, token=None, slack_channel=None):
        self.channel = (
            slack_channel if slack_channel is not None else os.getenv("SLACK_CHANNEL")
        )
        self.url = url if url is not None else os.getenv("SLACK_WEBHOOK_URL")
        slack_token = token if token is not None else os.getenv("SLACK_TOKEN")
        self.client = WebClient(slack_token)
        self.logger = logging.getLogger(__name__)

    def post_notification(self, messages=[]):
        """Post a notification to Slack."""
        # https://gist.github.com/mrjk/079b745c4a8a118df756b127d6499aa0
        blocks = []
        for message in messages:
            for chunk in _chunk_text(str(message)):
                blocks.append(
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": chunk,
                        },
                    }
                )
        with httpx.Client() as client:
            res = client.post(
                url=self.url,
                json={
                    "text": ", ".join(block["text"]["text"] for block in blocks),
                    "blocks": blocks,
                },
            )
            if res.status_code >= 300:
                self.logger.warning(
                    "Slack webhook rejected notification: %s %s",
                    res.status_code,
                    res.text,
                )

    def upload_test_results_file(self, filename, extension, results):
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
