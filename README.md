# Translator SRI Automated Test Harness
Automated Test Harness that downloads Translator Tests and executes them via Translator Runners

## Overview
The Test Harness is a wrapper around the Test Runners. Its job is to retrieve automated tests, run the given queries, and then pass the responses along to test runners, and then send the report to a test dashboard, all while being easily and automatically instantiated.

### Test Runners
The Test Harness incorporates Test Runners that run analyses on the responses of the automated tests. These Runners must be pip installable and take a test asset input and response as arguments. An example Test Runner function can be found [here](https://github.com/NCATSTranslator/ARS_Test_Runner/blob/master/ARS_Test_Runner/semantic_test.py#L196). The list of current Test Runners can be found in `requirements-runners.txt`.

### Test Schema
*_WARNING:_* This schema is likely to change as the Test Cases are finalized
- env: the environment to run the queries against. (dev, ci, test, prod)
- query_type: type of query to test. (treats(creative), upregulates, downregulates)
- expected_output: whether the output curie is good or bad. (TopAnswer, Acceptable, BadButForgivable, NeverShow)
- input_curie: curie used in the initial query.
- output_curie: curie checked for in the results

## How to use:
The Test Harness is a CLI that you need to install:
- `pip install -r requirements.txt` to install normal dependencies
- `pip install -r requirements-runners.txt` to install the Test Runners
- `pip install .` to install the Test Harness CLI

Once everything is installed, you can call
- `test-harness -h` to see available options

### Running locally
By default the Test Harness reports results to an Information Radiator and
posts them to Slack. To run everything locally without those services (for
example while developing), pass `--local`:
- `test-harness --local download <suite>`

In local mode the harness makes no network calls to the Information Radiator or
Slack. The test results (CSV and JSON) and any performance artifacts are saved
to a local directory instead (`test_results/` by default, configurable with
`--output_dir`).

You don't have to use `--local` to get local files: if Slack isn't configured
(no `SLACK_WEBHOOK_URL` / `SLACK_TOKEN` / `SLACK_CHANNEL`), the results are
saved to `--output_dir` automatically. Likewise, if the Information Radiator
isn't configured (no `ZE_BASE_URL` / `ZE_REFRESH_TOKEN`), the harness falls
back to a local reporter.

### Running local test files
`test-harness download <suite>` fetches the suites from
[NCATSTranslator/Tests](https://github.com/NCATSTranslator/Tests) every run,
which is no good when you want to change a CURIE and see what happens. A few
small, editable suites ship in `test_harness/test_suites/`; run one with `load`
instead of `download`:
- `test-harness --local load local_acceptance`

| Suite | What it is |
| --- | --- |
| `local_acceptance` | Two acceptance cases — one MVP1 (drug treats disease), one MVP2 (chemical affects gene) — so `--query_type` has something to filter. |
| `local_performance` | One `QuantitativeTest` case, run by HelmsDeep. |
| `local_pathfinder` | One Pathfinder case: two pinned endpoints, expecting a named node on a path between them. |

The suite name is the file's name without `.json`, the same way the downloaded
suites are named, so the same name works with either subcommand.

The harness reads `test_suites/` in the working directory when that exists —
so a suite you're editing in your checkout is found without a flag — and
otherwise the copy shipped with the package. `--tests_dir` overrides both:
- `test-harness --local load my_suite --tests_dir ~/scratch/suites`

These are ordinary Translator Testing Model `TestSuite` documents, identical in
shape to the downloaded ones. Copy one, change the CURIEs, run it. See
[`test_harness/test_suites/README.md`](test_harness/test_suites/README.md) for
which fields actually drive behavior — in particular, a performance case's
`test_run_time`/`spawn_rate` and its asset's query are inert, because HelmsDeep
owns the ramp and the corpus (see [Performance tests](#performance-tests)).

### Running one type of query
A suite normally mixes the MVP query types. To run just one of them — handy
for a local evaluation run of a change that only affects one — pass
`--query_type`:
- `test-harness --local --query_type MVP1 download <suite>`

- `MVP1` runs the drug treats disease queries (test assets with a
  `biolink:treats` predicate).
- `MVP2` runs the chemical affects gene queries (`biolink:affects`).
- Without the flag, every test in the suite is run, as before.

Filtering happens per test asset, so a test case that mixes both types
contributes only its matching assets, and a test case left with no assets is
dropped. Pathfinder test cases don't generate an MVP query, so they're skipped
whenever `--query_type` is given. Saved result filenames are prefixed with the
query type (eg `MVP1_test_report.json`) so runs of different slices stay
distinguishable.

### Result files
The acceptance results CSV has a row per test asset, with the asset's name, its
Information Radiator URL, the ARS pk, and the test case/asset ids. Every agent
then contributes four columns:
- `<agent>` - the agent's status for the asset (`PASSED`, `FAILED`,
  `NO_RESULTS`, `SKIPPED`, `ERROR`).
- `<agent>_found` - `true`/`false` for whether the expected answer was anywhere
  in that agent's response, regardless of whether that made the test pass. It
  is blank when the agent never got far enough for the question to have an
  answer (it was skipped, or errored before returning results).
- `<agent>_rank` - the rank the expected answer came back at (the ARS's own
  rank for ARS results, the result's position for an ARA's results). Blank when
  the expected answer wasn't found.
- `<agent>_score` - the score the expected answer came back with (the ARS
  sugeno score for ARS results, the analysis score for an ARA's). Blank when
  the expected answer wasn't found.

These are the same numbers as the `actual_output` in the per-test report JSON
uploaded to the Information Radiator.

### Performance tests
Performance tests (`QuantitativeTest` cases) are run by
[**HelmsDeep**](https://github.com/TranslatorSRI/HelmsDeep), installed from
`requirements-runners.txt`. The harness does not measure anything itself: it
picks the HelmsDeep run type for the test case, runs its CLI, and reports what
came back.

HelmsDeep owns two things the harness used to own, and this is deliberate:

- **The ramp.** HelmsDeep's `LoadTestShape` drives users, spawn rate, and
  duration from its own per-layer `stages` table, so a test case's
  `test_run_time` and `spawn_rate` are ignored. A run takes as long as the run
  type's ramp says (tens of minutes to over an hour). HelmsDeep can compress a
  run to a wall-clock budget, but its own docs are explicit that a compressed
  run is indicative rather than a measurement, so the harness never asks for it.
- **The queries.** HelmsDeep sends its own corpus and varies the pinned entity
  per request, so the numbers cover a real cost surface instead of warming one
  cache. The test asset's TRAPI query is not what goes under load; the
  Information Radiator log for the test records the *run plan* (layer, ramp,
  SLO, checkpoints) instead, which is what you need to read the result.

#### Which run type a test gets
The layer follows the component: `ars` uses HelmsDeep's async submit/poll/merge
targets, and every other component is queried as an ARA (a blocking
`POST /query`). The stack cascades ARS → ARAs → KPs, so exactly one layer is
loaded per run.

The query profile within that layer defaults to `default` — the layer's own
single-class corpus, which answers the open question *how far can we go?* and
reports a **knee** (max sustainable concurrency). Two heavier profiles are
available:

- `mixed` — the 2:1 inferred/Pathfinder acceptance profile. This is the only
  profile that carries **pass/fail checkpoints** (does 30 concurrent hold? 45?
  does 60 avoid substantial failures?).
- `pathfinder` — the two-pinned-endpoint path queries, the heaviest class.

Select one with `--performance_profile`, or per test case with a `mixed` /
`pathfinder` entry in its `test_runner_settings`; the flag wins over the test
case.
- `test-harness --local --performance_profile mixed download <suite>`

#### What gets reported
Two artifacts per run are uploaded to Slack (or saved to `--output_dir`):
- `<host>_<run_type>_summary.json` — HelmsDeep's `summary.json` verbatim. This
  is the authoritative result: the knee, every stage's numbers, the checkpoint
  verdicts, measurement-quality warnings, and (for ARS runs) health and red
  flags.
- `<host>_<run_type>_report.html` — locust's own HTML report: charts and
  request/failure tables.

Both are uploaded with a comment carrying the run's checkpoint verdict, and the
Slack summary message leads with the overall pass/fail. A run with no
checkpoints reports its knee and says so explicitly rather than claiming a pass
it never tested for. A missed checkpoint, or a HelmsDeep run that never produced
a summary at all, is reported as `FAILED` in the Information Radiator too, so
the dashboard agrees with Slack.

The rest of HelmsDeep's output (`_stages.csv`, `_by_qtype.csv`,
`_checkpoints.csv`, and the ARS debug logs) is left on disk under
`--output_dir` for digging in.

### Sweeping several services in one scheduled job
HelmsDeep loads exactly one layer per run, and the harness resolves one target
per invocation, so measuring the ARS and each ARA means several runs. They must
not overlap: the stack cascades ARS → ARAs → KPs, so loading two layers at once
double-loads whatever sits underneath and corrupts both measurements.

`test-harness-sweep` runs them sequentially in one process tree, which is what
lets a single Kubernetes Job (or `docker run`, or a local shell) do the whole
sweep:

```bash
test-harness-sweep --suite performance_tests --download \
    ars=https://ars.ci.transltr.io \
    aragorn=https://aragorn.ci.transltr.io \
    arax=https://arax.ci.transltr.io \
    bte=https://bte.ci.transltr.io \
    -- --performance_profile mixed
```

Each `NAME=URL` pair becomes one `test-harness --target_url URL --target NAME`
invocation, in the order given, and the sweep waits for each to finish before
starting the next. Anything after `--` is passed through to every invocation.
Targets can come from the `PERFORMANCE_TARGETS` environment variable instead, as
a comma-separated list of the same pairs — usually the easier knob in a CronJob,
since changing the sweep is then a manifest edit rather than an image rebuild.

Two behaviors worth knowing:
- **Every target runs even if an earlier one fails.** A broken ARS shouldn't
  cost you the ARA numbers. The sweep exits non-zero if any target failed, and
  prints a per-target summary with exit codes and durations.
- **Each target gets its own `--output_dir` subdirectory.** HelmsDeep names its
  raw files after the run type and test case id, neither of which mentions the
  host, so every ARA in a sweep writes the same `helmsdeep_aras_case_<id>_*`
  names and would otherwise overwrite the previous target's files.

[`deploy/cronjob.example.yaml`](deploy/cronjob.example.yaml) is a worked CronJob
for this, with the fields a multi-hour job actually needs — `concurrencyPolicy:
Forbid` so the next schedule can't start a second overlapping sweep,
`backoffLimit: 0` so a failure doesn't re-run hours of load, an
`activeDeadlineSeconds` ceiling, `PYTHONUNBUFFERED` so `kubectl logs` shows
progress, and CPU requests that keep the load generator from measuring its own
scheduling delay.

Budget the time before you schedule it: with `--performance_profile mixed` a
four-service sweep is roughly 70 min for the ARS plus 61 min per ARA (~4.2
hours); the single-class default profile is ~58 + ~35 × 3 (~2.7 hours). Nothing
overlaps, by design.

### Overriding the target service
Tests specify which component to run against (`ars`, `ara`, ...), and the
harness normally resolves those components to deployed services through the
SmartAPI registry. To run the tests against a service that isn't deployed yet —
for example a locally running ARA you want to check before releasing — you can
override the target with `--target_url` and `--target`:
- `test-harness --local --target_url http://localhost:8080 --target aragorn download <suite>`

With an override in place:
- Every query is sent directly to `--target_url`, regardless of the `components`
  specified in the tests, and the SmartAPI registry is not consulted.
- `--target` is the infores identifier of the service (with or without the
  `infores:` prefix). Any target other than `ars` is treated as a single
  service and queried with a `POST` to `<target_url>/query`; use `--target ars`
  to run a local ARS with the usual submit/poll flow.
- Pass/fail results are reported for the override target (instead of being
  driven by the ARS), and performance tests are pointed at the override URL.
- Saved result filenames (CSV/JSON results, performance artifacts, and the
  `--json_output` report) are prefixed with the target, e.g.
  `aragorn_test_report.json`, so runs against different services stay
  distinguishable.
