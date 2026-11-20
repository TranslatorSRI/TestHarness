"""The Slack report's shape: one message to read at a glance, with its image,
and the details in its thread."""

import logging

from test_harness.slacker import Attachment, LocalSlacker, Slacker


class _FakeClient:
    """Records what would have gone to Slack's Web API."""

    def __init__(self, not_ready=0, image_error="invalid_blocks"):
        self.posts, self.uploads = [], []
        self.not_ready = not_ready  # image posts rejected before the file is ready
        self.image_error = image_error

    def chat_postMessage(self, **kwargs):
        if any(b["type"] == "image" for b in kwargs["blocks"]) and self.not_ready:
            self.not_ready -= 1
            raise RuntimeError(
                f"The request to the Slack API failed: {self.image_error}"
            )
        self.posts.append(kwargs)
        return {"ts": f"msg{len(self.posts)}"}

    def files_upload_v2(self, **kwargs):
        self.uploads.append(kwargs)
        return {"files": [{"id": f"F{len(self.uploads)}"}]}


def _slacker(client):
    slacker = Slacker(url="https://hooks.slack.com/x", token="xoxb", slack_channel="C1")
    slacker.client = client
    slacker.IMAGE_DELAY_SECONDS = 0
    return slacker


CHART = Attachment("chart.png", b"\x89PNG", title="Chart")
RESULTS = Attachment("results.csv", b"a,b\n", "Test Results:")


def _top_level(client):
    return [p for p in client.posts if p["thread_ts"] is None]


def test_one_message_with_the_image_and_the_details_in_its_thread():
    client = _FakeClient(not_ready=2)  # Slack is still processing the upload
    ts = _slacker(client).post_thread(
        "*suite* finished", image=CHART, details=["details", " "], files=[RESULTS]
    )
    chart, results = client.uploads
    # the image is uploaded unshared, and shown in the report itself
    assert "channel" not in chart and chart["file"] == b"\x89PNG"
    [top] = _top_level(client)
    assert top["text"] == "*suite* finished"
    assert top["blocks"][-1]["slack_file"] == {"id": "F1"}
    assert ts == "msg1"
    # blank details are dropped; the rest, and the files, go in its thread
    [detail] = [p for p in client.posts if p["thread_ts"]]
    assert (detail["text"], detail["thread_ts"]) == ("details", "msg1")
    assert results["thread_ts"] == "msg1" and results["channel"] == "C1"


def test_without_an_image_the_summary_is_a_plain_message():
    client = _FakeClient()
    assert _slacker(client).post_thread("*suite* finished", details=["d"]) == "msg1"
    top, detail = client.posts
    assert top["thread_ts"] is None and top["text"] == "*suite* finished"
    assert detail["thread_ts"] == "msg1"


def test_an_image_slack_wont_show_leads_the_thread_instead(caplog):
    client = _FakeClient(not_ready=100)
    ts = _slacker(client).post_thread("summary", image=CHART, details=["d"])
    # still exactly one message in the channel, with the details under it
    [top] = _top_level(client)
    assert top["text"] == "summary" and ts == "msg1"
    assert all(b["type"] != "image" for b in top["blocks"])
    chart = client.uploads[-1]
    assert (chart["filename"], chart["thread_ts"]) == ("chart.png", "msg1")
    assert "posting it in the thread instead" in caplog.text


def test_only_an_unready_image_is_retried():
    client = _FakeClient(not_ready=1, image_error="not_in_channel")
    _slacker(client).post_thread("summary", image=CHART)
    # gave up on the image at once, and posted the report without it
    assert len(_top_level(client)) == 1 and client.not_ready == 0


def test_long_details_are_split_into_blocks():
    client = _FakeClient()
    _slacker(client).post_thread("s", details=["line\n" * 1500])
    assert len(client.posts[1]["blocks"]) > 1


def test_the_local_slacker_saves_the_image_and_files(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    slacker = LocalSlacker(output_dir=str(tmp_path))
    slacker.post_thread("summary", image=CHART, details=["detail"], files=[RESULTS])
    assert sorted(p.name for p in tmp_path.iterdir()) == ["chart.png", "results.csv"]
    assert "summary" in caplog.text and "detail" in caplog.text
