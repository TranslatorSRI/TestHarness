"""Client for the Information Radiator's ingest API.

Runs alongside the Zebrunner ``Reporter`` while the new radiator is brought up.
Unlike the Reporter, it never makes a call per test: results are buffered and
posted in batches as structured records (see ``radiator_schema``).

An unreachable radiator must never cost a run its results, so nothing here
raises. Every record is kept in ``payload`` whether or not it was uploaded;
when an upload fails the harness saves that payload to disk, and
``test-harness-radiator push`` replays it later. Every write is an upsert on
ids the client chose, so replaying a run that partly made it is safe.
"""

import json
import logging
import os
import sys
from argparse import ArgumentParser
from datetime import datetime
from typing import Optional

import httpx

from radiator_schema import (
    INGEST_PREFIX,
    AssetResult,
    PerformanceResult,
    ResultBatch,
    RunCreate,
    RunFinish,
    RunPayload,
)


class RadiatorClient:
    """Posts a run's results to the Information Radiator."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        enabled: bool = True,
        public_url: Optional[str] = None,
        batch_size: int = 50,
        logger: logging.Logger = logging.getLogger(__name__),
    ):
        """Initialize the client.

        With ``enabled=False``, or without a URL and token, nothing is sent:
        the client only records, which is what ``--local`` runs want.

        ``public_url`` (or RADIATOR_PUBLIC_URL) is where people open the
        radiator, for the links the harness logs and posts to Slack, when it
        differs from where the harness uploads to: eg ``http://radiator:8000``
        inside docker compose, but ``http://localhost:8000`` in a browser.
        """
        self.base_url = (base_url or os.getenv("RADIATOR_URL") or "").rstrip("/")
        self.public_url = (
            public_url or os.getenv("RADIATOR_PUBLIC_URL") or self.base_url
        ).rstrip("/")
        self.token = token or os.getenv("RADIATOR_TOKEN")
        self.enabled = enabled and bool(self.base_url and self.token)
        self.batch_size = batch_size
        self.logger = logger
        self.payload: Optional[RunPayload] = None
        # Set by the first failed upload. Nothing is sent after that: the run
        # is replayed from the saved payload instead, which avoids a half-run
        # in the radiator that looks complete.
        self.upload_failed = False
        self._pending = ResultBatch()
        self._client: Optional[httpx.Client] = None

    @staticmethod
    def is_configured(base_url=None, token=None) -> bool:
        """Return True if enough config exists to talk to the radiator."""
        return bool(
            (base_url or os.getenv("RADIATOR_URL"))
            and (token or os.getenv("RADIATOR_TOKEN"))
        )

    @property
    def run_url(self) -> Optional[str]:
        """Link to the run in the radiator UI, once a run is open."""
        if not self.enabled or self.payload is None:
            return None
        return f"{self.public_url}/runs/{self.payload.run.run_id}"

    def start_run(self, run: RunCreate):
        """Open a run."""
        self.payload = RunPayload(run=run)
        self._send("POST", "/runs", run.model_dump(mode="json"))

    def add_result(self, result: AssetResult):
        """Record one asset's result, uploading once a batch has built up."""
        self.payload.results.append(result)
        self._pending.results.append(result)
        if len(self._pending.results) >= self.batch_size:
            self.flush()

    def add_performance(self, result: PerformanceResult):
        """Record one performance run and upload it straight away.

        These come hours apart in a sweep, so they aren't held for a batch.
        """
        self.payload.performance.append(result)
        self._pending.performance.append(result)
        self.flush()

    def flush(self):
        """Upload whatever results are buffered."""
        if not (self._pending.results or self._pending.performance):
            return
        batch, self._pending = self._pending, ResultBatch()
        self._send(
            "POST",
            f"/runs/{self.payload.run.run_id}/results",
            batch.model_dump(mode="json"),
        )

    def finish_run(
        self, counts: Optional[dict] = None, finish: Optional[RunFinish] = None
    ):
        """Upload any remaining results and close the run.

        ``finish`` closes it with a given end time instead of now, for replays.
        """
        self.flush()
        self.payload.finish = finish or RunFinish(
            ended_at=datetime.now().astimezone(), counts=counts or {}
        )
        self._send(
            "PATCH",
            f"/runs/{self.payload.run.run_id}",
            self.payload.finish.model_dump(mode="json"),
        )
        if self._client is not None:
            self._client.close()
            self._client = None

    def save(self, output_dir: str, prefix: str = "") -> str:
        """Write the whole run to ``output_dir`` for replay; return the path."""
        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(
            output_dir, f"{prefix}radiator_{self.payload.run.run_id}.json"
        )
        with open(path, "w") as f:
            f.write(self.payload.model_dump_json(indent=2))
        return path

    @property
    def diff_url(self) -> Optional[str]:
        """Link to what changed since the previous run, once a run is open."""
        return f"{self.run_url}/diff" if self.run_url else None

    def summary(self) -> Optional[dict]:
        """The run against the previous one in its series (pass rates,
        regressions, fixes), or None if it isn't available."""
        res = self._read("summary")
        return res.json() if res is not None else None

    def history_png(self, runs: int = 30) -> Optional[bytes]:
        """The series' pass-rate history up to this run, as a PNG, or None."""
        res = self._read("history.png", params={"runs": runs})
        return res.content if res is not None else None

    def performance_png(self, runs: int = 30) -> Optional[bytes]:
        """Each of the run's services' max sustainable concurrency up to this
        run, as a PNG, or None."""
        res = self._read("performance.png", params={"runs": runs})
        return res.content if res is not None else None

    def _read(self, what: str, params: Optional[dict] = None):
        """GET something about this run from the read API. Best effort: only
        once the whole run has been uploaded, and never raises."""
        if not self.enabled or self.upload_failed or self.payload is None:
            return None
        if self.payload.finish is None:
            return None
        return self._get(
            f"/api/runs/{self.payload.run.run_id}/{what}", params, f"the run's {what}"
        )

    # --- run cycles and the weekly digest ------------------------------------

    def cycle_url(self, cycle_id) -> Optional[str]:
        return f"{self.public_url}/cycles/{cycle_id}" if self.enabled else None

    @property
    def weekly_url(self) -> Optional[str]:
        return f"{self.public_url}/weekly" if self.enabled else None

    def cycle(self, cycle_id) -> Optional[dict]:
        """A run cycle's board (each test against its previous run, changes by
        agent, flags), or None if it isn't available."""
        res = self._get(f"/api/cycles/{cycle_id}", what="the run cycle")
        return res.json() if res is not None else None

    def cycle_board_png(self, cycle_id) -> Optional[bytes]:
        res = self._get(f"/api/cycles/{cycle_id}/board.png", what="the cycle board")
        return res.content if res is not None else None

    def digest(self, days: int = 7) -> Optional[dict]:
        """Every environment's latest runs over the last ``days``, or None."""
        res = self._get("/api/digest", {"days": days}, "the weekly digest")
        return res.json() if res is not None else None

    def digest_png(self, days: int = 7) -> Optional[bytes]:
        res = self._get("/api/digest.png", {"days": days}, "the weekly digest")
        return res.content if res is not None else None

    def _get(self, path: str, params: Optional[dict] = None, what: str = "it"):
        """GET from the read API. Best effort: never raises."""
        if not self.enabled:
            return None
        try:
            res = self._http().get(f"{self.base_url}{path}", params=params)
            res.raise_for_status()
            return res
        except Exception as e:
            self.logger.warning(
                f"Couldn't get {what} from the Information Radiator: {e}"
            )
            return None

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=f"{self.base_url}{INGEST_PREFIX}",
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=30,
                # retries connection failures only; an HTTP error is an answer
                transport=httpx.HTTPTransport(retries=2),
            )
        return self._client

    def _send(self, method: str, path: str, body: dict):
        if not self.enabled or self.upload_failed:
            return
        try:
            res = self._http().request(method, path, json=body)
            res.raise_for_status()
        except Exception as e:
            self.upload_failed = True
            self.logger.error(
                f"Upload to the Information Radiator failed ({method} {path}): "
                f"{e}. Not sending the rest of this run; it will be saved "
                "locally for `test-harness-radiator push`."
            )


def push(payload: RunPayload, client: RadiatorClient) -> bool:
    """Replay a saved run into the radiator. Returns True on success."""
    client.start_run(payload.run)
    for result in payload.results:
        client.add_result(result)
    for result in payload.performance:
        client.add_performance(result)
    if payload.finish is not None:
        # keep the original end time and counts, not the time of the replay
        client.finish_run(finish=payload.finish)
    else:
        # the run never finished (eg the harness crashed); leave it open
        client.flush()
    return not client.upload_failed


def cli():
    """Entrypoint for ``test-harness-radiator``."""
    parser = ArgumentParser(description="Information Radiator tools")
    subparsers = parser.add_subparsers(dest="command", required=True)
    push_parser = subparsers.add_parser(
        "push",
        help="Upload a run saved by the harness (radiator_<run id>.json)",
    )
    push_parser.add_argument("files", nargs="+", help="Saved run payload(s)")
    digest_parser = subparsers.add_parser(
        "digest",
        help=(
            "Post the weekly digest to Slack: every environment's latest runs "
            "of each test, with flags (needs the SLACK_* settings)"
        ),
    )
    digest_parser.add_argument(
        "--days", type=int, default=7, help="How far back to look (default 7)"
    )
    digest_parser.add_argument(
        "--output_dir",
        default="test_results",
        help="Where to save the digest if Slack isn't configured",
    )
    parser.add_argument("--radiator_url", help="Defaults to $RADIATOR_URL")
    parser.add_argument("--radiator_token", help="Defaults to $RADIATOR_TOKEN")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("test-harness-radiator")
    if not RadiatorClient.is_configured(args.radiator_url, args.radiator_token):
        logger.error("Set RADIATOR_URL and RADIATOR_TOKEN (or pass the flags).")
        sys.exit(1)

    if args.command == "digest":
        from test_harness.cycle import digest_cli

        client = RadiatorClient(args.radiator_url, args.radiator_token, logger=logger)
        sys.exit(digest_cli(client, args.days, args.output_dir, logger))

    failed = 0
    for path in args.files:
        with open(path) as f:
            payload = RunPayload.model_validate(json.load(f))
        client = RadiatorClient(args.radiator_url, args.radiator_token, logger=logger)
        if push(payload, client):
            logger.info(f"Pushed {path} -> {client.run_url}")
        else:
            failed += 1
    sys.exit(1 if failed else 0)
