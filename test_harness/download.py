"""Download tests."""
import logging
from pathlib import Path
from typing import List

from .models import TestCase


def download_tests(url: Path) -> List[TestCase]:
    """Download tests from specified location."""
    raise NotImplementedError()
