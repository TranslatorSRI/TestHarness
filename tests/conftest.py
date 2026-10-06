"""Fixtures shared by every test."""

import pytest

from test_harness.radiator_client import RadiatorClient


@pytest.fixture(autouse=True)
def isolate_radiator(monkeypatch, tmp_path):
    """Keep the Information Radiator client out of the developer's environment.

    ``main`` saves the run for the radiator whenever it isn't uploaded, which
    in tests is always. Saves aimed at the default ``test_results/`` land in
    the test's tmp dir instead of the checkout, and a radiator configured in
    the developer's shell is never used.
    """
    monkeypatch.delenv("RADIATOR_URL", raising=False)
    monkeypatch.delenv("RADIATOR_TOKEN", raising=False)
    save = RadiatorClient.save

    def save_to_tmp(self, output_dir, prefix=""):
        if output_dir == "test_results":
            output_dir = str(tmp_path)
        return save(self, output_dir, prefix)

    monkeypatch.setattr(RadiatorClient, "save", save_to_tmp)
