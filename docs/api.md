# Reading results from the Information Radiator

Every result the test harness records can be read from the radiator's JSON
API. **Reading needs no login and no token**: plain `GET` requests work from
a script, a notebook or `curl`.

The examples use `RADIATOR` for the radiator's address, eg

```bash
export RADIATOR=https://radiator.example.org
```

The full, interactive reference (every endpoint, parameter and field) is at
`$RADIATOR/api/docs`, and the OpenAPI spec at `$RADIATOR/api/openapi.json`.

## Quick start

```bash
curl "$RADIATOR/api/runs?suite=mvp_tests&env=ci&limit=5"
```

```python
import requests

RADIATOR = "https://radiator.example.org"

latest = requests.get(
    f"{RADIATOR}/api/runs/latest",
    params={"suite": "mvp_tests", "env": "ci", "results": "true"},
).json()
for asset in latest["results"]:
    for agent in asset["agents"]:
        print(asset["asset_id"], agent["agent"], agent["status"], agent["rank"])
```

Or straight into pandas, one row per asset and agent:

```python
import pandas as pd

df = pd.read_csv(f"{RADIATOR}/api/runs/{run_id}/results?format=csv")
```

## Endpoints

### Finding runs

| Request | Returns |
|---|---|
| `GET /api/runs` | Runs, newest first (without their results) |
| `GET /api/runs/latest?suite=&env=` | The newest finished run of a suite; `finished=false` includes one still going, `results=true` includes its results |

`/api/runs` takes any of:

| Parameter | |
|---|---|
| `suite`, `env` | eg `mvp_tests`, `ci` (`dev`, `ci`, `test`, `prod`) |
| `since`, `until` | When the run started, as ISO 8601 (eg `2026-11-01` or `2026-11-01T06:00:00Z`); `until` is exclusive |
| `origin` | `harness`, or `zebrunner_import` for the history brought over from Zebrunner |
| `zebrunner_launch` | The old Zebrunner `testRunId`: finds the run it was imported as |
| `limit`, `offset` | Paging: up to 500 runs at a time (default 50) |

Each run has its `run_id` (use it in the requests below), `number` (the `#`
on the dashboard), `suite`, `env`, `started_at`, `ended_at` (empty while the
run is going), `counts` (assets by status), `zebrunner_launch` for imported
runs, and the rest of what the harness recorded about it.

### A run's results

| Request | Returns |
|---|---|
| `GET /api/runs/{run_id}` | The run with every result, nested: each asset, with each agent's result inside it (`?results=false` for just the run) |
| `GET /api/runs/{run_id}/results` | The same results flattened: one row per asset and agent |
| `GET /api/runs/{run_id}/pks` | Every pk in the run: one row per asset and agent, with the asset's parent pk |

`/results` and `/pks` take `format=csv` for a CSV file instead of JSON, and
`agent=` (eg `ars`, `aragorn`) to keep only that agent's rows. `/results`
also takes `status=` (the agent's status, eg `FAILED`) and `test_case_id=`.
In CSV, a list (eg `output_curies`) is joined with `;`.

What each asset has:

| Field | |
|---|---|
| `test_case_id`, `asset_id` | From the Tests repo; stable across runs |
| `name`, `kind` | `kind` is `acceptance` or `pathfinder` |
| `expected_output` | `TopAnswer`, `Acceptable`, `BadButForgivable` or `NeverShow` |
| `predicate`, `input_curie`, `output_curies` | What was asked, and the CURIEs that count as the expected answer |
| `status` | The overall result: `PASSED`, `FAILED`, `NO_RESULTS`, `ERROR` or `SKIPPED` (`asset_status` in the flat rows) |
| `parent_pk` | The ARS parent pk of the query |
| `details` | Anything type-specific, eg Pathfinder's expected path nodes (nested JSON only) |

And each agent's result:

| Field | |
|---|---|
| `agent` | `ars`, or the ARA the ARS fanned out to |
| `status`, `message` | Its result, and why |
| `found`, `found_curies` | Whether the expected answer came back, and as which CURIEs |
| `rank`, `score` | Where the expected answer ranked, and its score |
| `n_results`, `http_status`, `response_time_s` | When recorded (response time only for direct queries) |
| `pk` | Its pk: the merged pk for `ars`, the child pk for an ARA |
| `expected_nodes_found`, `ndcg` | Pathfinder only: which expected path nodes were found, and the NDCG (0 to 1) of how high the first path with them ranked in the agent's top 500 |

A pk opens in the ARAX UI at `https://arax.ci.transltr.io/?r=<pk>`.

### Everything else

| Request | Returns |
|---|---|
| `GET /api/runs/{run_id}/summary` | Pass rate against the previous run of the same series, regressions, fixes |
| `GET /api/runs/{run_id}/progress` | How far a run still going has got |
| `GET /api/live` | The runs in progress |
| `GET /api/cycles`, `GET /api/cycles/{cycle_id}` | Run cycles, and a cycle's board |
| `GET /api/digest?days=7` | Every environment's latest runs over the week |
| `…/history.png`, `…/performance.png`, `…/board.png`, `/api/digest.png` | The charts posted to Slack |

## Coming from Zebrunner

The radiator records structured results, so the calls you made to Zebrunner
collapse into one, with no token:

| Zebrunner | Radiator |
|---|---|
| `POST /api/iam/v1/auth/refresh` with a refresh token, then `Authorization: Bearer <access token>` on every call | Nothing: drop the auth step and the header |
| `POST /api/reporting/api/tests/search` with `testRunId`, `page`, `pageSize` | `GET /api/runs/{run_id}` (nested) or `GET /api/runs/{run_id}/results` (flat). One call returns the whole run; there's no paging |
| `GET /api/reporting/v1/test-runs/{id}/tests/{testId}/logs`, then parsing the JSON report out of the log lines | Not needed: rank, score, found, pks and each agent's message are fields on every result |
| Zebrunner's labels (`TestCase`, `TestAsset`, `ExpectedOutput`, `InputCurie`, …) | `test_case_id`, `asset_id`, `expected_output`, `input_curie`, `output_curies` |

Run ids are different: the radiator's are UUIDs. To find the run an old
Zebrunner launch was imported as, `GET /api/runs?zebrunner_launch=<testRunId>`.
For new runs, find them by suite and environment (`/api/runs?suite=&env=`, or
`/api/runs/latest`) or copy the id from the run's page on the dashboard
(`$RADIATOR/runs/<run_id>`).

Before:

```python
token = requests.post(
    f"{ZEBRUNNER}/api/iam/v1/auth/refresh", json={"refreshToken": REFRESH_TOKEN}
).json()["authToken"]
headers = {"Authorization": f"Bearer {token}"}
tests = requests.post(
    f"{ZEBRUNNER}/api/reporting/api/tests/search",
    headers=headers,
    json={"page": 1, "pageSize": 1000, "testRunId": test_run},
).json()["results"]
for test in tests:
    logs = requests.get(
        f"{ZEBRUNNER}/api/reporting/v1/test-runs/{test_run}/tests/{test['id']}/logs",
        headers=headers,
        params={"page": 1, "pageSize": 10000},
    ).json()
    ...  # find the report in the log lines and parse it
```

After:

```python
run = requests.get(f"{RADIATOR}/api/runs/{run_id}").json()
for asset in run["results"]:
    for agent in asset["agents"]:
        ...  # agent["status"], agent["rank"], agent["score"], agent["pk"], ...
```

## Stability

Fields may be added to these responses, but none will be renamed or removed
without notice, so read the fields you need and ignore the rest.
