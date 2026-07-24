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
