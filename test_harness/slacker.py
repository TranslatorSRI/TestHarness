"""Slack notification integration class."""

import json
import logging
import os
import re
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

    @staticmethod
    def is_configured(url=None, token=None, slack_channel=None):
        """Return True if enough config exists to talk to Slack.

        Falls back to the same environment variables the constructor uses, so
        callers can decide whether to use a real Slacker or a LocalSlacker
        without instantiating one first. A webhook URL is required to post
        notifications and a token + channel to upload result files.
        """
        has_webhook = bool(url or os.getenv("SLACK_WEBHOOK_URL"))
        has_uploads = bool(token or os.getenv("SLACK_TOKEN")) and bool(
            slack_channel or os.getenv("SLACK_CHANNEL")
        )
        return has_webhook and has_uploads

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

    def upload_binary_file(self, filename, content, initial_comment=None, title=None):
        """Upload a binary file (PNG, HTML, etc.) to Slack."""
        with tempfile.TemporaryDirectory() as td:
            tmp_path = os.path.join(td, filename)
            with open(tmp_path, "wb") as f:
                f.write(content)
            self.client.files_upload_v2(
                channel=self.channel,
                title=title or filename,
                file=tmp_path,
                initial_comment=initial_comment or "Performance report:",
            )


def _slugify_filename(name):
    """Make ``name`` safe to use as a filename (no spaces/colons/slashes)."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", str(name)).strip("_")
    return slug or "test_results"


class LocalSlacker(Slacker):
    """A Slacker that writes results to disk instead of posting to Slack.

    Lets developers run the harness without a Slack workspace. Notifications
    are logged and result/artifact files are saved under ``output_dir`` so the
    CSV, JSON, and performance artifacts are still available locally.
    """

    def __init__(self, output_dir="test_results", logger=None):
        # Intentionally skip Slacker.__init__: no Slack client/config needed.
        self.output_dir = output_dir
        self.logger = logger if logger is not None else logging.getLogger(__name__)

    def _unique_path(self, filename):
        """Return a path in ``output_dir`` that doesn't clobber an existing file.

        Several results can share a base name (eg the acceptance and
        performance JSON summaries), so append a counter rather than silently
        overwriting a previously saved file.
        """
        os.makedirs(self.output_dir, exist_ok=True)
        base, ext = os.path.splitext(filename)
        candidate = os.path.join(self.output_dir, filename)
        counter = 1
        while os.path.exists(candidate):
            candidate = os.path.join(self.output_dir, f"{base}_{counter}{ext}")
            counter += 1
        return candidate

    def post_notification(self, messages=[]):
        """Log notifications instead of posting them to Slack."""
        for message in messages:
            self.logger.info(message)

    def upload_test_results_file(self, filename, extension, results):
        """Save a results file locally instead of uploading it to Slack."""
        path = self._unique_path(f"{_slugify_filename(filename)}.{extension}")
        with open(path, "w") as f:
            if extension == "csv":
                f.write(results)
            elif extension == "json":
                json.dump(results, f, indent=2)
            else:
                f.write(str(results))
        self.logger.info(f"Saved test results to {path}")
        return path

    def upload_binary_file(self, filename, content, initial_comment=None, title=None):
        """Save a binary artifact locally instead of uploading it to Slack."""
        path = self._unique_path(_slugify_filename(filename))
        with open(path, "wb") as f:
            f.write(content)
        self.logger.info(f"Saved artifact to {path}")
        return path
