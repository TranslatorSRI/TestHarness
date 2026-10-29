# Information Radiator

A replacement for the Zebrunner-based Information Radiator, built in this repo
alongside the harness. The harness posts structured results to it; it shows
them to the consortium behind one shared login.

## Why

Zebrunner's model forced the harness to put its results into labels and log
lines: per-agent status went in as labels, and the full report (found, rank,
score, pks) as a JSON string in a log. None of it could be filtered or charted,
performance results never reached it, and each test cost about five API calls.
The radiator stores results as records, so it can show what changed between
runs and how assets and agents trend over time.

## Decisions

| | |
|---|---|
| Repo | Monorepo: `radiator/` here, shipped as its own image (`ghcr.io/translatorsri/testharness-radiator`). |
| Hosting | Same Kubernetes namespace as the harness CronJob. |
| UI login | One shared username/password for the consortium (a k8s secret). Signed session cookie; failed logins are throttled per client. |
| API auth | Every API route needs the bearer token (`RADIATOR_API_TOKEN`); the read API also accepts a logged-in session. Uploads need the token. |
| Raw responses | Not stored. Results keep their pks, which open in the ARAX UI on ci. |
| History | Imported from Zebrunner's API (`radiator.zebrunner_import`). |

## What's where

```
radiator_schema/          the API contract (Pydantic), shared by both sides
test_harness/
  radiator_client.py      harness side: batched uploads, save + `test-harness-radiator push`,
                          and `test-harness-radiator digest`
  cycle.py                `test-harness-cycle`: a run cycle and its Slack board
radiator/
  app.py, auth.py         FastAPI app, login, token auth
  api.py, ingest.py       ingest + read API; upserts
  models.py, migrations/  Postgres schema (SQLAlchemy, Alembic)
  queries.py, web.py      the dashboard's queries and pages
  templates/, static/     server-rendered HTML, CSS, a small hover script
  charts.py, grid.py      server-side SVG charts, the run grid
  history_png.py          the pass-rate and concurrency PNGs for Slack
  cycles.py, board_png.py run cycles: the board, flags, the weekly digest (and its PNGs)
  zebrunner_import.py     the history import
  demo.py                 made-up data for development
deploy/
  helm/                           Helm charts for the radiator and the harness
  radiator.example.yaml           Deployment (+ migration init container), Service, Ingress
  radiator-postgres.example.yaml  a small in-namespace Postgres, if you need one
  radiator-import.example.yaml    one-off Job for the Zebrunner import
  cycle.example.yaml              weekly run cycle CronJob, one per environment
  digest.example.yaml             Monday digest CronJob
```

## The dashboard

- **Weekly**: each environment's latest full runs of acceptance, pathfinder
  and performance over a week, against the run before, with a verdict
  (regressed, improved, mixed, steady) and the flags; step back a week at a
  time. Links to each run cycle.
- **A run cycle**: the board for one environment's cycle (each test against
  its previous run, with a step line of its last 8 runs), the flags, and the
  regressions and fixes by agent.
- **Live updates**: while a run is going, its page fills in by itself as the
  harness uploads each test case's results, with a progress bar (assets tested
  out of the run's total) and "so far" on the numbers. The runs list and the
  Weekly page pick up runs starting, uploading and finishing. Pages poll a
  small JSON check (`/api/runs/{id}/progress`, `/api/live`) every 10 to 60
  seconds, pause while the tab is hidden, and refetch only when something
  changed. An open run that hasn't uploaded anything for 2 hours is marked
  *didn't finish* and no longer polled. Performance runs have nothing to show
  until HelmsDeep finishes.
- **Runs**: every run, newest first, with its status breakdown, pass rate, and
  duration; filter by suite and environment.
- **A run**: pass rate against the previous run, regressions and fixes, a
  per-agent breakdown (also split by expected output), and the results grid:
  every asset against every agent, with the rank the expected answer came back
  at. Filter by status (overall or
  per agent), expected output, or search by name/id/CURIE. Each cell opens its
  pk in ARAX.
- **What changed**: against the previous run *of the same series* (same suite,
  env, target override, and query type): regressions, fixes, other status
  changes, skips, rank moves of 5+ places, and assets added or dropped.
- **TRAPI version**: each run records the TRAPI version it spoke (shown on the
  run and in the runs list). A run whose version differs from its previous
  run's says so on its page, in What changed, in the cycle board's flags and
  in the Slack headline, since the change may be the upgrade, not the services.
  Runs from before it was recorded (and imported ones) show none.
- **An asset**: its status per agent across the last 60 runs, the rank of the
  expected answer and the number of results over time, and a table of runs.
- **Trends**: each agent's pass rate over full runs of a suite, overall and by
  expected output. Skips are left out; runs with a target override or a single
  query type aren't included.
- **Run grid**: every full acceptance run of a suite (no target override, no
  single query type), one lane per environment, over the last 7–90 days. Each
  card shows the run's number, date, pass rate, its change against the
  previous run in that environment, and passed/failed/skipped counts; its color
  says whether the pass rate went up, down, or held (within 0.1 pts), or that
  it's the first run in the environment. Runs close together stack, and a line
  joins each run to the next in its lane. Environments in
  `RADIATOR_EXCLUDED_ENVS` (default `dev`) are greyed out and marked excluded.
  Every run gets a number from the radiator (`#123`), in order of when it
  started; imported runs are numbered oldest first.
- **Performance**: HelmsDeep's max sustainable concurrency per service over
  time (per host, run type, profile, and environment), with checkpoint
  verdicts.

## In the Slack report

Everything below goes to `SLACK_CHANNEL` once Zebrunner is gone. While
Zebrunner is configured (`ZE_*`), `SLACK_CHANNEL` stays Zebrunner's, with the
report it always got, and everything below goes to the radiator's own channel
(`RADIATOR_SLACK_WEBHOOK_URL`, `RADIATOR_SLACK_CHANNEL`), or isn't posted
without one (see the README's
[A Slack channel for the new radiator](../README.md#a-slack-channel-for-the-new-radiator)).

When the radiator has the run, the harness's Slack report gains a headline
against the previous run of the same series, and a chart of the series up to
this run is posted under it:

- **Acceptance runs:** pass rate and its change, regressions, fixes, and a link
  to what changed; the chart is each agent's pass rate over the last 30 runs.
- **Performance runs:** for each service, its max sustainable concurrency and
  its change since the service's previous run, and the checkpoint verdict; the
  chart is that concurrency over the service's last 30 runs, with missed
  checkpoints marked. A service's series is one host under one HelmsDeep run
  type and profile, in one environment, since a `mixed` and a `default` run load
  it differently.

The charts use step lines: a run's value holds until the next run, so every rise
and drop shows plainly (the dashboard's trend charts do the same). The radiator
draws them (`history.png`, `performance.png`); the harness fetches them with its
token and uploads them, since Slack can't reach pages behind the login. Without
the radiator, or if it can't be reached, the report is what it always was.

### Run cycles and the weekly digest

`test-harness-cycle` runs acceptance, pathfinder and performance in one
environment and marks their runs with a shared `cycle_id`. When it finishes, it
posts the cycle's board (`/api/cycles/{id}/board.png`) and a message with each
test's headline, the regressions and fixes by agent, and the flags. The
digest, `test-harness-radiator digest`, posts `/api/digest.png` and the same
kind of message for every environment. See the README's
[Run cycles](../README.md#run-cycles-and-the-weekly-digest) for the command and
the flags.

How the board is worked out (`radiator/cycles.py`):

- **Which test a run is**: a run with performance results is performance; one
  whose results are all pathfinder assets is pathfinder; anything else is
  acceptance.
- **Compared with what**: each run against the previous finished run of its
  series (same suite, environment, target override and query type), which is
  last week's cycle in practice. Performance compares per service.
- **Verdict**: acceptance and pathfinder by regressions against fixes (agent
  results that went from passing to not, or back): more regressions is
  *Regressed*, more fixes *Improved*, an equal number *Mixed*, none *Steady*.
  Performance by max sustainable concurrency: down 10% or more (or newly
  missed checkpoints) is *Regressed*, up 10% or more *Improved*.
- **Flags**: thresholds are at the top of `cycles.py`, to tune as we learn
  what's noise.
- **The digest** takes each environment's latest full run of each suite over
  the week (no `--query_type`; acceptance and pathfinder without a target
  override) and each performance target's latest run, whether or not a cycle
  ran them.

## API

Uploads go under `/api/ingest`, with `Authorization: Bearer <token>`:

| Method | Path | Body |
|---|---|---|
| `POST` | `/api/ingest/runs` | `RunCreate` |
| `POST` | `/api/ingest/runs/{run_id}/results` | `ResultBatch` |
| `PATCH` | `/api/ingest/runs/{run_id}` | `RunFinish` |

Every write is an upsert: runs on the client-generated `run_id`, results on
`(run_id, test_case_id, asset_id)`, and an asset's agent rows are replaced by
the newest upload. Retries and replays never duplicate. The asset ids come from
the Tests repo and are stable across runs, which is what history and trends key
on.

Reading, with the token or a session:

| Path | |
|---|---|
| `GET /api/runs?suite=&env=&limit=` | runs, newest first |
| `GET /api/runs/{run_id}` | a run with every result (`?results=false` for just the run) |
| `GET /api/runs/{run_id}/progress` | how far a run has got (assets tested of its total, counts, state); what its page polls |
| `GET /api/live` | the runs in progress, and a version that changes when runs start, upload or finish |
| `GET /api/runs/{run_id}/summary` | pass rate, previous pass rate, regressions, fixes |
| `GET /api/cycles?limit=20` | the latest run cycles |
| `GET /api/cycles/{cycle_id}` | a cycle's board: each test against its previous run, changes by agent, flags |
| `GET /api/cycles/{cycle_id}/board.png` | the board posted to Slack |
| `GET /api/digest?days=7&until=` | every environment's latest runs over the week |
| `GET /api/digest.png?days=7&until=` | the digest posted to Slack |
| `GET /api/runs/{run_id}/history.png?runs=30` | the pass-rate chart posted to Slack |
| `GET /api/runs/{run_id}/performance.png?runs=30` | the concurrency chart posted to Slack |

## Deploying

The Helm charts in [`deploy/helm`](../deploy/helm/README.md) do all of this:
`radiator` (the app, migrations, Service, Ingress, optionally Postgres and the
Zebrunner import) and `test-harness` (the run cycles, digest and sweep). Copy
each chart's `values.example.yaml` to a git-ignored `values.<name>.yaml`, fill
it in, and `helm upgrade --install`. Without Helm, the steps with the plain
manifests are:

1. **Database.** Use an existing Postgres, or apply
   `deploy/radiator-postgres.example.yaml`. Back it up: it's the only copy of
   the history.
2. **Secrets.** Create `radiator-secrets` as described at the top of
   `deploy/radiator.example.yaml`.
3. **The radiator.** Adapt and apply `deploy/radiator.example.yaml` (image tag,
   hostname, TLS secret, ingress class). Migrations run in an init container on
   every start; they're a no-op when the schema is current.
4. **The harness.** Add `RADIATOR_URL` and `RADIATOR_TOKEN` (the same
   api-token) to `test-harness-secrets`; `deploy/cronjob.example.yaml` already
   reads them. `RADIATOR_URL` can be the in-cluster service
   (`http://radiator`); then also set `RADIATOR_PUBLIC_URL` to the address
   people open, since the links in Slack are built from it.
   The harness then reports to both Zebrunner and the radiator.
5. **History.** Run the Zebrunner import (below).
6. **Cutover**, after a couple of weeks of both: point people at the new UI,
   then remove the Zebrunner `Reporter` and the `ZE_*` secrets. If the new
   radiator had its own Slack channel meanwhile (`RADIATOR_SLACK_*`), point
   `SLACK_*` at it and drop `RADIATOR_SLACK_*`.

The image is built and pushed by the release workflow alongside the harness's.
To build it by hand, from the repo root:
`docker build -f radiator/Dockerfile -t testharness-radiator .`

## Importing from Zebrunner

```
python -m radiator.zebrunner_import \
  --zebrunner-url "$ZE_BASE_URL" --refresh-token "$ZE_REFRESH_TOKEN" \
  --radiator-url https://radiator.example.org --radiator-token "$RADIATOR_TOKEN" \
  --since 2026-09-15 --dry-run --limit 5
```

Drop `--dry-run`/`--limit` once that looks right; `deploy/radiator-import.example.yaml`
runs it in the cluster. It reads Zebrunner's API (launches, their tests'
labels, and each test's logs) and posts runs like the harness does.

- **What comes across:** test case/asset ids, expected output, CURIEs, every
  agent's status (from the labels the harness set), and from the logged report,
  each agent's found/rank/score and the pks. `--no-logs` skips the logs: much
  faster, but no ranks, scores or pks.
- **The environment** was never sent to Zebrunner, so it's worked out per
  run: a pk only exists on the ARS that made it (and the harness asked the
  ARS to retain each response), so the import asks each environment's ARS
  (from the SmartAPI registry; `--ars ENV=URL` to override) for one of the
  run's pks, and the one that has it is the run's environment. A run whose
  pks no ARS has falls back to prod if it has prod-only agents, else no
  environment. The log says which for each run, and the summary counts them.
  `--env` records every run under one environment instead (no lookups).
- **What can't:** result counts, HTTP status and response times were never
  recorded, so those show as "not recorded", never zero.
- **Skipped:** performance tests (pre-HelmsDeep, no summary).
- **Re-runnable:** imported runs get ids derived from the Zebrunner launch, so
  a second run skips them, or updates them with `--force`.
- **Zebrunner versions:** its read API changed between versions. The import
  speaks both: the v1 API (reporting-service 1.4x and later), which needs
  `--project-id` (the `projectId` in the API calls Zebrunner's launches page
  makes; your browser's network tab shows them), and the legacy API (eg
  1.28). By default it tries v1 and falls back; `--api v1|legacy` picks one.
  On the legacy API it logs the fields of the first launch and test it
  reads, so a dry run shows if anything is named differently.

The v1 endpoints were read from Zebrunner's published images
(`zebrunner/reporting-service` 1.54.1, `zebrunner/test-execution-logs-service`
1.6.1); the legacy ones were checked against a 1.28 instance. Another version
may differ; `--dry-run --limit 5` shows quickly whether it works against yours.

## Developing

The quickest way to a running radiator is Docker Compose, from the repo root:
`docker compose up --build` (see the README's
[Trying it out with Docker Compose](../README.md#trying-it-out-with-docker-compose)).
To work on the radiator's code itself, without Docker:

```
# a Postgres to develop against, then:
export RADIATOR_DATABASE_URL=postgresql+psycopg://postgres@localhost:5432/radiator
export RADIATOR_API_TOKEN=dev-token RADIATOR_USERNAME=dev RADIATOR_PASSWORD=dev \
       RADIATOR_SESSION_SECRET=dev RADIATOR_SECURE_COOKIES=false
pip install -r radiator/requirements-test.txt
alembic -c radiator/alembic.ini upgrade head
python -m radiator.demo          # made-up history to look at
uvicorn radiator.app:create_app --factory --reload
```

Tests need a database they may wipe:
`RADIATOR_TEST_DATABASE_URL=... pytest radiator/tests`. After changing
`models.py`, add a migration with
`alembic -c radiator/alembic.ini revision --autogenerate -m "..."`, review it,
and check `alembic -c radiator/alembic.ini check` is clean (CI runs it).

To point a local harness run at it: `RADIATOR_URL=http://localhost:8000
RADIATOR_TOKEN=dev-token test-harness --env ci load local_acceptance`.

## When a run, or part of one, is missing from the dashboard

Every run's log says where its results went and what arrived:

- At the start: `Reporting to the Information Radiator at <url>: <run link>`,
  or `Not reporting to the Information Radiator (--local / RADIATOR_URL and
  RADIATOR_TOKEN aren't set)`.
- At the end: `Information Radiator: <run link> has 612 of the 640 assets in
  the suite`, then, if any are missing, a warning naming them
  (`TestCase_12/Asset_3, ...`). Search the log for one of those ids: every
  place an asset can be left out logs it with its test case and asset id
  and why: a test case with no assets or objective, an expected output the
  harness doesn't test, a query that couldn't be built (`couldn't build the
  query for asset ...`), no answer from a service (`No answer from <agent> at
  <url>: ConnectError ...`, recorded with the harness's 418), or a record the
  schema rejected (`couldn't record asset ... for the Information Radiator`).
- If an upload failed: `Upload to the Information Radiator failed (POST <url>,
  N asset results ...)` with the radiator's answer (eg the field a 422
  rejected) or the connection error. Nothing more is sent for that run; the
  whole run is saved as `radiator_<run id>.json`, to replay with
  `test-harness-radiator push`.

The radiator's own log has a line for each run opened, each batch of results
and each run finished, and, for a rejected upload, which fields were wrong
(`Rejected upload POST ...: body.results.0.status: ...`). Set
`RADIATOR_LOG_LEVEL` (the chart's `config.logLevel`) to change how much it says.

## Known gaps

- ARA response times under the ARS aren't recorded: the harness only sees
  ARS fan-out through 10s polling. The ARS's own message timestamps could
  give them.
- HelmsDeep's `report.html` isn't uploaded yet (it still goes to Slack); the
  radiator stores the `summary.json` it is drawn from.
