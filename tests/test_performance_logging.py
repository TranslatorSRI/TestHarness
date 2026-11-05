"""What a performance run tells the log while HelmsDeep works."""

import json
import logging
import sys
import textwrap

from test_harness import performance_test_runner as ptr


def _fake_helmsdeep(tmp_path, monkeypatch, body):
    """Stand a script in for ``python -m helmsdeep.cli``."""
    (tmp_path / "fake_helmsdeep.py").write_text(textwrap.dedent(body))
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    monkeypatch.setattr(ptr, "HELMSDEEP_MODULE", "fake_helmsdeep")


def test_helmsdeep_output_reaches_the_log_as_it_runs(tmp_path, monkeypatch, caplog):
    _fake_helmsdeep(
        tmp_path,
        monkeypatch,
        """
        import json, sys
        prefix = sys.argv[sys.argv.index("--csv-prefix") + 1]
        print("stage 1/3 started: 10 users")
        print("locust: request failed", file=sys.stderr)
        stages = [{"stage": 0, "users": 10, "requests": 4, "errors": 4}]
        with open(prefix + "_summary.json", "w") as f:
            json.dump({"stages": stages, "max_sustainable_concurrency": None}, f)
        sys.exit(1)
        """,
    )
    caplog.set_level(logging.INFO)
    results = ptr.run_helmsdeep(
        "aras", "https://example.org", str(tmp_path / "out" / "run")
    )
    assert results["error"] is None and results["exit_code"] == 1
    messages = [r.getMessage() for r in caplog.records]
    assert "[helmsdeep] stage 1/3 started: 10 users" in messages
    assert "[helmsdeep] locust: request failed" in messages
    assert any(
        "HelmsDeep finished" in m and "4 requests, 4 errors" in m for m in messages
    )
    assert any("Every request HelmsDeep sent failed" in m for m in messages)


def test_output_is_kept_for_a_run_that_wrote_no_summary(tmp_path, monkeypatch):
    _fake_helmsdeep(
        tmp_path,
        monkeypatch,
        """
        import sys
        print("Traceback: config error", file=sys.stderr)
        sys.exit(2)
        """,
    )
    results = ptr.run_helmsdeep("aras", "https://example.org", str(tmp_path / "run"))
    assert "without writing" in results["error"]
    assert "Traceback: config error" in results["output"]


def test_a_host_carrying_the_endpoint_path_is_flagged(caplog):
    caplog.set_level(logging.INFO)
    ptr.check_host("ars_mixed", "https://shepherd.example.org/ars")
    messages = [r.getMessage() for r in caplog.records]
    assert (
        "HelmsDeep will send its requests to "
        "https://shepherd.example.org/ars/ars/api/submit" in messages
    )
    [warning] = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert "https://shepherd.example.org." in warning.getMessage()


def test_a_base_url_host_is_not_flagged(caplog):
    caplog.set_level(logging.INFO)
    ptr.check_host("ars_mixed", "https://ars.ci.transltr.io")
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_the_runs_trapi_version_reaches_helmsdeep(tmp_path, monkeypatch, caplog):
    _fake_helmsdeep(
        tmp_path,
        monkeypatch,
        """
        import os
        print("trapi=" + os.environ.get("HELMSDEEP_TRAPI_VERSION", "unset"))
        """,
    )
    monkeypatch.delenv("HELMSDEEP_TRAPI_VERSION", raising=False)
    caplog.set_level(logging.INFO)
    ptr.run_helmsdeep(
        "aras", "https://example.org", str(tmp_path / "run"), trapi_version="2.0.0"
    )
    messages = [r.getMessage() for r in caplog.records]
    assert "[helmsdeep] trapi=2.0.0" in messages
    assert "HelmsDeep will write its queries in TRAPI 2.0.0" in messages
