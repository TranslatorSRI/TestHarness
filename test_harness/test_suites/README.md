# Local test suites

Editable test suites for running the harness without downloading anything.
`test-harness load <suite>` reads `<suite>.json` from here; `<suite>` is the
file name without the extension.

```bash
test-harness --local load local_acceptance
test-harness --local load local_performance
test-harness --local load local_pathfinder
```

These are ordinary [Translator Testing Model](https://github.com/TranslatorSRI/translator-testing-model)
`TestSuite` documents — the same shape as the suites in
[NCATSTranslator/Tests](https://github.com/NCATSTranslator/Tests/tree/main/test_suites),
just small enough to read and edit. Copy one, change the CURIEs, and run it.

## Where the harness looks

`test_suites/` in your working directory wins when it exists, so a suite you're
editing in your own checkout is found without a flag. Otherwise this directory
(shipped with the package) is used. `--tests_dir` overrides both:

```bash
test-harness --local load my_suite --tests_dir ~/scratch/suites
```

## What's here

| File | What it exercises |
| --- | --- |
| `local_acceptance.json` | Two acceptance cases: MVP1 (drug **treats** disease, one `TopAnswer` + one `NeverShow` asset) and MVP2 (chemical **affects** gene). Enough of a mix that `--query_type MVP1` / `MVP2` has something to filter. |
| `local_performance.json` | One `QuantitativeTest` case, run by [HelmsDeep](https://github.com/TranslatorSRI/HelmsDeep). |
| `local_pathfinder.json` | One Pathfinder case: two pinned endpoints, expecting a named node on at least one path between them. |

## Editing them

The fields that usually matter:

- **`test_env`** (`dev`/`ci`/`test`/`prod`) and **`components`** decide which
  deployed service the queries go to. `--target_url` / `--target` override both.
- **`input_id`** / **`output_id`** are the CURIEs under test. They must be ones
  the target environment actually knows about, or you'll get empty results that
  look like failures.
- **`expected_output`** (`TopAnswer`, `Acceptable`, `BadButForgivable`,
  `NeverShow`) is what the asset is asserting, and drives pass/fail. Assets with
  any other value are skipped, so a typo here silently drops the asset.
- **`predicate_id`** picks the query template: `biolink:treats` builds an MVP1
  query, `biolink:affects` an MVP2 one. The qualifiers have to agree with it —
  an `affects` asset needs a real aspect/direction qualifier.

### Performance cases specifically

HelmsDeep owns the ramp and the queries, so most of a performance case is
inert:

- **`test_run_time` and `spawn_rate` are ignored.** HelmsDeep's load shape
  drives users, spawn rate, and duration from its own per-layer stage table. A
  run takes as long as that ramp says — tens of minutes to over an hour.
- **The asset is not the query under load.** HelmsDeep sends its own corpus and
  varies the pinned entity per request, so changing `input_id` won't change what
  gets sent. The asset still identifies the test case in the dashboard.
- **`test_runner_settings` picks the profile.** `mixed` runs the 2:1
  inferred/Pathfinder acceptance profile — the only one with pass/fail
  checkpoints — and `pathfinder` runs the path-finding corpus. Anything else
  gets the layer's single-class corpus, which reports a knee and no checkpoints.
  `--performance_profile` overrides whatever the case says.
- **`components`** picks the layer: `ars` runs HelmsDeep's async
  submit/poll/merge targets, anything else is loaded as an ARA.

So to run the checkpointed profile against a local ARA:

```bash
test-harness --local --performance_profile mixed \
    --target_url http://localhost:8080 --target aragorn \
    load local_performance
```
