# Information Radiator replacement

A custom replacement for the Zebrunner-based Information Radiator, built in this
repo alongside the harness.

## Why

Zebrunner's model forces the harness to put its results into labels and log
lines: per-agent status goes in as labels, and the full report (found, rank,
score, pks) as a JSON string in a log. None of it can be filtered or charted,
performance results never reach it at all, and each test costs about five API
calls. The replacement stores results as structured records, so it can show
what changed between runs and how assets and agents trend over time.

## Decisions

| | |
|---|---|
| Repo | Monorepo: the radiator lives here, in its own package and image. |
| Hosting | Same Kubernetes namespace as the harness CronJob. |
| UI login | One shared username/password for the consortium, from a k8s secret. Handled by the app (login form + signed session cookie); no external identity provider. |
| API auth | Ingest requires a bearer token (`RADIATOR_TOKEN`), separate from the UI login. Every API route requires authentication. |
| Raw responses | Not stored. Records keep the pks, which link to the ARS/ARAX UI. |
| History | Past runs are imported from Zebrunner. |

## Layout

```
radiator_schema/        API contract (Pydantic), shared by harness and radiator
test_harness/
  radiator_client.py    harness-side client + `test-harness-radiator push`
radiator/               (phase 2) FastAPI service, DB, importer, UI, Dockerfile
deploy/radiator.yaml    (phase 5) Deployment, Service, Ingress
```

## API

All writes go under `/api/ingest`, with `Authorization: Bearer <token>`:

| Method | Path | Body |
|---|---|---|
| `POST` | `/api/ingest/runs` | `RunCreate` |
| `POST` | `/api/ingest/runs/{run_id}/results` | `ResultBatch` |
| `PATCH` | `/api/ingest/runs/{run_id}` | `RunFinish` |

Every write is an upsert: runs on the client-generated `run_id`, results on
`(run_id, test_case_id, asset_id)`. So retries and replays never duplicate. The
asset ids come from the Tests repo and are stable across runs, which is what
the trend views key on (`hash_test_asset` is not stable across processes).

## Phases

1. **Contract + harness (done on `claude/radiator-phase1`).** `radiator_schema`;
   the harness records n_results, HTTP status, and response time (direct
   queries only, since ARS fan-out is only seen through 10s polling) per agent;
   it dual-writes to Zebrunner and the new radiator, and saves runs it couldn't
   upload for `test-harness-radiator push`.
2. **Radiator backend.** FastAPI + Postgres (SQLAlchemy, Alembic); ingest
   endpoints; read endpoints shaped for the views; shared-login session auth for
   the UI and read API, bearer token for ingest; an artifact endpoint for
   HelmsDeep's `report.html`.
3. **UI**, in priority order: run matrix (assets x agents with status, rank,
   score, n_results, pk links); diff against the last comparable run (same
   suite, env, and target); asset history; agent pass rate over time by
   expected-output category; performance trends (knee, checkpoints per host).
4. **Zebrunner import.** Runs -> runs, tests -> assets via the
   `TestCase`/`TestAsset`/`ExpectedOutput` labels, agent labels -> statuses,
   found/rank/score from the report log where present. Older runs lack those,
   and all lack n_results and response time; the UI shows them as not
   recorded, not zero. Imported runs carry `origin: zebrunner_import`, and the
   import is re-runnable.
5. **Deploy + cutover.** Deploy to the namespace; dual-write for 2-3 weeks;
   point Slack at the new UI; remove `Reporter` and the `ZE_*` secrets.

## Known gaps

- ARA response times under the ARS aren't recorded. The ARS's own message
  timestamps would give them; that needs confirming against the ARS API.
- A performance test whose HelmsDeep run raises (rather than returning an
  error) isn't recorded in either radiator. This is existing behavior.
