"""The radiator's log: every upload, and why one was rejected."""

import logging
import uuid

import pytest

import radiator_schema as schema

from . import factories as f


class _Records(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.records = []

    def emit(self, record):
        self.records.append(record)

    @property
    def text(self):
        return "\n".join(r.getMessage() for r in self.records)


@pytest.fixture
def radiator_log():
    # the radiator's logger doesn't propagate (it has its own handler)
    logger = logging.getLogger("radiator")
    handler = _Records()
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(logging.INFO)
    yield handler
    logger.removeHandler(handler)
    logger.setLevel(previous)


def test_uploads_are_logged(api, radiator_log):
    run = f.run_create(expected_assets=2, target="aragorn")
    api.post("/api/ingest/runs", json=run.model_dump(mode="json"))
    batch = schema.ResultBatch(results=[f.asset("A1"), f.asset("A2")])
    api.post(
        f"/api/ingest/runs/{run.run_id}/results", json=batch.model_dump(mode="json")
    )
    api.patch(f"/api/ingest/runs/{run.run_id}", json=f.finish().model_dump(mode="json"))
    lines = [r.getMessage() for r in radiator_log.records]
    assert lines == [
        f"Run {run.run_id} opened: sprint_4_tests in ci, target aragorn, "
        "2 assets expected",
        f"Run {run.run_id}: 2 asset results, 0 performance results",
        f"Run {run.run_id} finished",
    ]


def test_a_rejected_upload_says_what_was_wrong(api, radiator_log):
    run = f.run_create()
    api.post("/api/ingest/runs", json=run.model_dump(mode="json"))
    bad = {"results": [{"test_case_id": "TC", "asset_id": "A1", "status": "MAYBE"}]}
    res = api.post(f"/api/ingest/runs/{run.run_id}/results", json=bad)
    assert res.status_code == 422
    warning = radiator_log.records[-1].getMessage()
    assert warning.startswith(
        f"Rejected upload POST /api/ingest/runs/{run.run_id}/results: "
    )
    assert "body.results.0.status" in warning and "body.results.0.kind" in warning


def test_results_for_an_unknown_run_are_logged(api, radiator_log):
    run_id = uuid.uuid4()
    res = api.post(f"/api/ingest/runs/{run_id}/results", json={"results": []})
    assert res.status_code == 404
    assert f"Upload for unknown run {run_id}" in radiator_log.text


def test_the_read_api_and_pages_dont_log_validation_noise(api, browser, radiator_log):
    browser.get("/runs/not-a-uuid")
    api.get("/api/runs", params={"limit": "lots"})
    assert "Rejected upload" not in radiator_log.text
