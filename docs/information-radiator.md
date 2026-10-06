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
  radiator_client.py      harness side: batched uploads, save + `test-harness-radiator push`
radiator/
  app.py, auth.py         FastAPI app, login, token auth
  api.py, ingest.py       ingest + read API; upserts
  models.py, migrations/  Postgres schema (SQLAlchemy, Alembic)
  queries.py, web.py      the dashboard's queries and pages
  templates/, static/     server-rendered HTML, CSS, a small hover script
  charts.py               server-side SVG charts
  zebrunner_import.py     the history import
  demo.py                 made-up data for development
deploy/
  radiator.example.yaml           Deployment (+ migration init container), Service, Ingress
  radiator-postgres.example.yaml  a small in-namespace Postgres, if you need one
  radiator-import.example.yaml    one-off Job for the Zebrunner import
```

## The dashboard

- **Runs**: every run, newest first, with its status breakdown, pass rate, and
  duration; filter by suite and environment.
- **A run**: pass rate against the previous run, regressions and fixes, a
  per-agent breakdown, and the results grid: every asset against every agent,
  with the rank the expected answer came back at. Filter by status (overall or
  per agent), expected output, or search by name/id/CURIE. Each cell opens its
  pk in ARAX.
- **What changed**: against the previous run *of the same series* (same suite,
  env, target override, and query type): regressions, fixes, other status
  changes, skips, rank moves of 5+ places, and assets added or dropped.
- **An asset**: its status per agent across the last 60 runs, the rank of the
  expected answer and the number of results over time, and a table of runs.
- **Trends**: each agent's pass rate over full runs of a suite, overall and by
  expected output. Skips are left out; runs with a target override or a single
  query type aren't included.
- **Performance**: HelmsDeep's max sustainable concurrency per service over
  time (per host, run type, profile, and environment), with checkpoint
  verdicts.

## In the Slack report

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
| `GET /api/runs/{run_id}/summary` | pass rate, previous pass rate, regressions, fixes |
| `GET /api/runs/{run_id}/history.png?runs=30` | the pass-rate chart posted to Slack |
| `GET /api/runs/{run_id}/performance.png?runs=30` | the concurrency chart posted to Slack |

## Deploying

1. **Database.** Use an existing Postgres, or apply
   `deploy/radiator-postgres.example.yaml`. Back it up: it's the only copy of
   the history.
2. **Secrets.** Create `radiator-secrets` as described at the top of
   `deploy/radiator.example.yaml`.
3. **The radiator.** Adapt and apply `deploy/radiator.example.yaml` (image tag,
   hostname, TLS secret, ingress class). Migrations run in an init container on
   every start; they're a no-op when the schema is current.
4. **The harness.** Add `RADIATOR_URL` (the radiator's **public** URL: it's
   also the link posted to Slack) and `RADIATOR_TOKEN` (the same api-token) to
   `test-harness-secrets`; `deploy/cronjob.example.yaml` already reads them.
   The harness then reports to both Zebrunner and the radiator.
5. **History.** Run the Zebrunner import (below).
6. **Cutover**, after a couple of weeks of both: point people at the new UI,
   then remove the Zebrunner `Reporter` and the `ZE_*` secrets.

The image is built and pushed by the release workflow alongside the harness's.
To build it by hand, from the repo root:
`docker build -f radiator/Dockerfile -t testharness-radiator .`

## Importing from Zebrunner

```
python -m radiator.zebrunner_import \
  --zebrunner-url "$ZE_BASE_URL" --refresh-token "$ZE_REFRESH_TOKEN" \
  --radiator-url https://radiator.example.org --radiator-token "$RADIATOR_TOKEN" \
  --env ci --dry-run --limit 5
```

Drop `--dry-run`/`--limit` once that looks right; `deploy/radiator-import.example.yaml`
runs it in the cluster. It reads Zebrunner's API (launches, their tests'
labels, and each test's logs) and posts runs like the harness does.

- **What comes across:** test case/asset ids, expected output, CURIEs, every
  agent's status (from the labels the harness set), and from the logged report,
  each agent's found/rank/score and the pks. `--no-logs` skips the logs: much
  faster, but no ranks, scores or pks.
- **What can't:** result counts, HTTP status and response times were never
  recorded, so those show as "not recorded", never zero. The environment was
  never sent to Zebrunner: pass `--env`, or runs with prod-only agents are
  marked prod and the rest get no environment. To import several environments,
  run once per environment, narrowing with `--suite`, `--since`, `--until`.
- **Skipped:** performance tests (pre-HelmsDeep, no summary).
- **Re-runnable:** imported runs get ids derived from the Zebrunner launch, so
  a second run skips them, or updates them with `--force`.
- **If your Zebrunner needs it**, pass `--project-id`.

The endpoints were read from Zebrunner's published images
(`zebrunner/reporting-service` 1.54.1, `zebrunner/test-execution-logs-service`
1.6.1). An older instance may differ; `--dry-run --limit 5` shows quickly
whether it works against yours.

## Developing

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
RADIATOR_TOKEN=dev-token test-harness load local_acceptance`.

## Known gaps

- ARA response times under the ARS aren't recorded: the harness only sees
  ARS fan-out through 10s polling. The ARS's own message timestamps could
  give them.
- HelmsDeep's `report.html` isn't uploaded yet (it still goes to Slack); the
  radiator stores the `summary.json` it is drawn from.
