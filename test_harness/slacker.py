import httpx
import os


class Slacker:
    """Slack notification poster."""

    def __init__(self, url=None):
        self.url = url if url else os.getenv("SLACK_WEBHOOK_URL")

    async def post_notification(self, messages=None):
        """Post a notification to Slack."""
        # https://gist.github.com/mrjk/079b745c4a8a118df756b127d6499aa0
        if messages is None:
            messages = []
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
