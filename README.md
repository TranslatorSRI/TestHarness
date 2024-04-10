# Translator SRI Automated Test Harness
Automated Test Harness that downloads Translator Tests and executes them via Translator Runners

## Overview
The Test Harness is a lightweight wrapper around the Test Runners. Its job is to retrieve automated tests, pass them along to test runners, and then send the report to a test dashboard, all while being easily and automatically instantiated.

### Test Runners
The Test Harness incorporates Test Runners that do the actual running of the automated tests. These Runners must be pip installable and take a test input that is defined by this Test Harness. The list of current Test Runners can be found in `requirements-runners.txt`.

### Test Schema
*_WARNING:_* This schema is likely to change as the Test Cases are finalized
- env: the environment to run the queries against. (dev, ci, test, prod)
- query_type: type of query to test. (treats(creative), upregulates, downregulates)
- expected_output: whether the output curie is good or bad. (TopAnswer, Acceptable, BadButForgivable, NeverShow)
- input_curie: curie used in the initial query.
- output_curie: curie checked for in the results

## How to use:
The Test Harness is a CLI that you need to install (preferably within a suitable Python Virtual Environment):
- `pip install -r requirements.txt` to install normal dependencies
- `pip install -r requirements-runners.txt` (or requirements-runners-win64.txt) to install the Test Runners 
- `pip install .` to install the Test Harness CLI

Once everything is installed, you can call
- `test-harness -h` to see available options
