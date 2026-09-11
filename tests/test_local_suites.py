"""Tests for the local test suites and the `load` subcommand that reads them.

These cover reading a suite off disk instead of downloading a zip, and assert
that every suite shipped in ``test_harness/test_suites`` actually parses into
the test-case types the harness dispatches on -- a broken example suite is only
discovered at run time otherwise.
"""

import json
import logging

from translator_testing_model.datamodel.pydanticmodel import (
    PathfinderTestCase,
    PerformanceTestCase,
    TestCase,
)

from test_harness.download import (
    BUNDLED_TESTS_DIR,
    load_tests,
    resolve_tests_dir,
)
from test_harness.utils import filter_tests_by_query_type

from .helpers.logger import setup_logger

logger = setup_logger()


def test_every_bundled_suite_parses():
    """Each shipped suite loads and yields at least one test case."""
    suites = sorted(p.stem for p in BUNDLED_TESTS_DIR.glob("*.json"))
    assert suites, "no local test suites are shipped with the package"
    for suite in suites:
        tests = load_tests(suite, None, logger)
        assert tests, f"{suite} loaded no test cases"
        for test in tests.values():
            assert test.test_assets, f"{suite} has a test case with no assets"
            assert test.test_case_objective is not None


def test_bundled_suites_cover_each_runner():
    """The examples exist to be run, so each harness path needs one."""
    acceptance = load_tests("local_acceptance", None, logger)
    performance = load_tests("local_performance", None, logger)
    pathfinder = load_tests("local_pathfinder", None, logger)

    assert all(
        type(t) is TestCase and t.test_case_objective == "AcceptanceTest"
        for t in acceptance.values()
    )
    # The acceptance suite carries both MVP types so --query_type has something
    # to filter; without that the flag can't be exercised locally at all.
    assert len(filter_tests_by_query_type(acceptance, "MVP1", logger)) == 1
    assert len(filter_tests_by_query_type(acceptance, "MVP2", logger)) == 1

    assert all(
        isinstance(t, PerformanceTestCase)
        and t.test_case_objective == "QuantitativeTest"
        for t in performance.values()
    )
    assert all(isinstance(t, PathfinderTestCase) for t in pathfinder.values())


def test_tests_dir_overrides_the_bundled_copy(tmp_path):
    """An explicit --tests_dir wins, so a scratch suite can be run from anywhere."""
    suites = tmp_path / "suites"
    suites.mkdir()
    source = json.loads((BUNDLED_TESTS_DIR / "local_acceptance.json").read_text())
    source["id"] = "scratch"
    (suites / "scratch.json").write_text(json.dumps(source))

    tests = load_tests("scratch", str(suites), logger)
    assert len(tests) == 2


def test_cwd_test_suites_wins_over_the_bundled_copy(tmp_path, monkeypatch):
    """A checkout's own test_suites/ is found without passing a flag."""
    (tmp_path / "test_suites").mkdir()
    monkeypatch.chdir(tmp_path)
    assert resolve_tests_dir(None).resolve() == (tmp_path / "test_suites").resolve()
    # ...and with nothing in the working directory, the shipped copy is used.
    monkeypatch.chdir(tmp_path.parent)
    assert resolve_tests_dir(None) == BUNDLED_TESTS_DIR


def test_a_missing_suite_reports_what_is_available(tmp_path, caplog):
    """A typo'd suite name has to say what the options were, not just fail."""
    with caplog.at_level(logging.ERROR):
        assert load_tests("does_not_exist", None, logger) == {}
    assert "local_acceptance" in caplog.text

    caplog.clear()
    with caplog.at_level(logging.ERROR):
        assert load_tests("whatever", str(tmp_path / "nope"), logger) == {}
    assert "doesn't exist" in caplog.text


def test_an_unparseable_suite_fails_loudly(tmp_path, caplog):
    """A hand-edited suite with a bad field must not look like an empty run."""
    (tmp_path / "broken.json").write_text('{"id": "broken", "test_cases": 5}')
    with caplog.at_level(logging.ERROR):
        assert load_tests("broken", str(tmp_path), logger) == {}
    assert "Failed to parse" in caplog.text
