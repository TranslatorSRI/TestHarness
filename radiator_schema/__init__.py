"""The contract between the Test Harness and the Information Radiator.

Both sides import these models: the harness builds them and posts them, the
radiator validates what it receives against them. Changing a model here changes
the API, so a change that is not purely additive needs ``SCHEMA_VERSION``
bumped and the radiator taught to read both versions.

Identity is chosen so that every write is an idempotent upsert, which is what
makes it safe to retry an upload or replay a saved payload:

* a run is keyed by ``run_id``, a UUID the *client* generates, so re-posting a
  run updates it instead of creating a second one;
* a result is keyed by ``(run_id, test_case_id, asset_id)``, the ids from the
  Tests repo. They are stable across runs, which is also what lets the
  radiator line an asset's results up over time. (``hash_test_asset`` is not:
  it uses Python's per-process salted ``hash()``.)

Fields the harness can't know are ``None``, never a stand-in like ``0`` or
``""``: "not recorded" and "zero results" have to stay distinguishable,
especially in runs imported from Zebrunner, which predate most of this.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1

# Every write the harness makes goes under this prefix, authenticated with a
# bearer token. The rest of the radiator sits behind the consortium login.
INGEST_PREFIX = "/api/ingest"


class Status(str, Enum):
    """Outcome of a test, or of a single agent's part in one.

    Mirrors ``test_harness.utils.AgentStatus``; redeclared so this package
    stays importable without the harness.
    """

    PASSED = "PASSED"
    FAILED = "FAILED"
    NO_RESULTS = "NO_RESULTS"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"


class RunCreate(BaseModel):
    """Opens a run. Posted once, before any results."""

    run_id: UUID
    suite: str
    env: Optional[str] = None
    # Set when the run overrode the services named in the tests, eg to test a
    # locally running ARA. Runs with and without an override are different
    # series and must not be compared as if they were the same thing.
    target: Optional[str] = None
    target_url: Optional[str] = None
    query_type: Optional[str] = None
    harness_version: Optional[str] = None
    # Where the suite came from: the Tests repo archive URL, or a local dir.
    tests_source: Optional[str] = None
    started_at: datetime
    # "harness" for live runs; the Zebrunner importer marks its runs so the UI
    # can say why they are missing fields.
    origin: Literal["harness", "zebrunner_import"] = "harness"
    # The run's id in the system it came from, for imported runs.
    origin_ref: Optional[str] = None


class AgentResult(BaseModel):
    """One agent's part in one test asset."""

    agent: str
    status: Status
    message: Optional[str] = None
    # HTTP status, or the ARS's status code for an ARA it fanned out to.
    http_status: Optional[int] = None
    # Whether the expected output came back anywhere in this agent's results,
    # and the rank/score it came back with (ARS sugeno for the ARS, the ARA's
    # own analysis score for an ARA).
    found: Optional[bool] = None
    rank: Optional[int] = None
    score: Optional[float] = None
    n_results: Optional[int] = None
    # Only recorded where the harness can measure it honestly (direct queries).
    # ARS fan-out is polled every 10s, so timing it from the harness would
    # mostly measure the polling.
    response_time_s: Optional[float] = None
    pk: Optional[str] = None
    # Pathfinder only: which expected path nodes were found.
    expected_nodes_found: Optional[str] = None


class AssetResult(BaseModel):
    """The result of one test asset, across every agent it was sent to."""

    test_case_id: str
    asset_id: str
    kind: Literal["acceptance", "pathfinder"]
    name: Optional[str] = None
    expected_output: Optional[str] = None
    predicate: Optional[str] = None
    # acceptance: input -> output; pathfinder: source -> target
    input_curie: Optional[str] = None
    output_curie: Optional[str] = None
    # The overall status, driven by the ARS (or the override target).
    status: Status
    parent_pk: Optional[str] = None
    agents: List[AgentResult] = Field(default_factory=list)
    # Anything type-specific that doesn't merit a column yet, eg Pathfinder's
    # expected path nodes.
    details: Dict[str, Any] = Field(default_factory=dict)


class PerformanceResult(BaseModel):
    """One HelmsDeep run against one host."""

    test_case_id: str
    asset_id: str
    host: str
    helmsdeep_target: Optional[str] = None
    profile: Optional[str] = None
    status: Status
    # Set when the run never produced a summary at all.
    error: Optional[str] = None
    exit_code: Optional[int] = None
    max_sustainable_concurrency: Optional[float] = None
    knee_unsupported: Optional[bool] = None
    # None when the run had no checkpoints to pass or fail.
    checkpoints_passed: Optional[bool] = None
    # HelmsDeep's summary.json, verbatim. The fields above are lifted out of it
    # for querying; this stays the authoritative copy.
    summary: Dict[str, Any] = Field(default_factory=dict)


class ResultBatch(BaseModel):
    """A batch of results for an open run. Posted as many times as needed."""

    results: List[AssetResult] = Field(default_factory=list)
    performance: List[PerformanceResult] = Field(default_factory=list)


class RunFinish(BaseModel):
    """Closes a run."""

    ended_at: datetime
    # Overall status counts, as reported in Slack.
    counts: Dict[str, int] = Field(default_factory=dict)


class RunPayload(BaseModel):
    """Everything about a run in one document.

    This is what the harness saves locally (``--local``, or when an upload
    fails) and what ``test-harness-radiator push`` replays.
    """

    schema_version: int = SCHEMA_VERSION
    run: RunCreate
    results: List[AssetResult] = Field(default_factory=list)
    performance: List[PerformanceResult] = Field(default_factory=list)
    finish: Optional[RunFinish] = None
