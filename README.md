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
