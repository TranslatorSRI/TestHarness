"""setup_logger runs when the harness is imported, so it must not crash on the log file."""

import logging

import pytest

from test_harness.logger import setup_logger


@pytest.fixture(autouse=True)
def restore_logging():
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    for handler in root.handlers:
        if handler not in handlers:
            handler.close()
    root.handlers, root.level = handlers, level


def test_makes_the_log_directory(tmp_path, monkeypatch):
    # the image has no logs/ (.dockerignore leaves it out)
    monkeypatch.chdir(tmp_path)
    setup_logger()
    assert (tmp_path / "logs" / "harness.log").exists()


def test_falls_back_to_the_console(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "logs").write_text("not a directory")
    setup_logger()
    assert [type(h) for h in logging.getLogger().handlers] == [logging.StreamHandler]
    # dictConfig replaces caplog's handler; the console handler writes to stderr
    assert "logging to the console only" in capsys.readouterr().err
