"""The Slack report's shape: one message to read at a glance, with its image,
and the details in its thread."""

import logging

from test_harness.slacker import Attachment, LocalSlacker, Slacker


class _FakeClient:
    """Records what would have gone to Slack's Web API."""

    def __init__(self, share_after=0):
        self.posts, self.uploads = [], []
        self.share_after = share_after  # files.info calls before the share shows
        self.info_calls = 0

    def chat_postMessage(self, **kwargs):
        self.posts.append(kwargs)
        return {"ts": f"msg{len(self.posts)}"}

    def files_upload_v2(self, **kwargs):
        self.uploads.append(kwargs)
        return {"files": [{"id": f"F{len(self.uploads)}"}]}

    def files_info(self, file):
        self.info_calls += 1
        if self.info_calls <= self.share_after:
            return {"file": {"shares": {}}}
        return {"file": {"shares": {"public": {"C1": [{"ts": "img.1"}]}}}}


def _slacker(client):
    slacker = Slacker(url="https://hooks.slack.com/x", token="xoxb", slack_channel="C1")
    slacker.client = client
    slacker.SHARE_DELAY_SECONDS = 0
    return slacker


CHART = Attachment("chart.png", b"\x89PNG", title="Chart")
RESULTS = Attachment("results.csv", b"a,b\n", "Test Results:")


def test_the_image_carries_the_summary_and_the_details_go_in_its_thread():
    client = _FakeClient(share_after=2)  # Slack shares the upload a bit later
    ts = _slacker(client).post_thread(
        "*suite* finished", image=CHART, details=["details", " "], files=[RESULTS]
    )
    assert ts == "img.1"
    first, second = client.uploads
    assert first["initial_comment"] == "*suite* finished"
    assert first["file"] == b"\x89PNG" and "thread_ts" not in first
    assert second["thread_ts"] == "img.1"
    assert second["filename"] == "results.csv"
    # blank details are dropped; the rest go in the thread
    [post] = client.posts
    assert (post["text"], post["thread_ts"]) == ("details", "img.1")


def test_without_an_image_the_summary_is_a_message():
    client = _FakeClient()
    assert _slacker(client).post_thread("*suite* finished", details=["d"]) == "msg1"
    top, detail = client.posts
    assert top["thread_ts"] is None and top["text"] == "*suite* finished"
    assert detail["thread_ts"] == "msg1"


def test_details_still_stay_out_of_the_channel_if_the_share_never_shows(caplog):
    client = _FakeClient(share_after=100)
    _slacker(client).post_thread("summary", image=CHART, details=["d"])
    holder, detail = client.posts
    assert holder["thread_ts"] is None and "Details" in holder["text"]
    assert detail["thread_ts"] == "msg1"
    assert "Couldn't find the Slack message" in caplog.text


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
