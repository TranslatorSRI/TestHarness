"""Setup file for SRI Test Harness package."""

from setuptools import setup

with open("README.md", encoding="utf-8") as readme_file:
    readme = readme_file.read()

setup(
    name="sri-test-harness",
    version="0.8.0",
    author="Max Wang",
    author_email="max@covar.com",
    url="https://github.com/TranslatorSRI/TestHarness",
    description="Translator SRI Test Harness",
    long_description_content_type="text/markdown",
    long_description=readme,
    # radiator_schema is the API contract shared with the Information
    # Radiator; it ships with the harness so the two can't drift apart.
    packages=["test_harness", "radiator_schema"],
    include_package_data=True,
    # The editable local test suites ship with the package so `test-harness
    # load <suite>` works from a plain (non-editable) install too, not just
    # from a checkout.
    package_data={"test_harness": ["test_suites/*.json", "test_suites/README.md"]},
    zip_safe=False,
    license="MIT",
    python_requires=">=3.9",
    entry_points={
        "console_scripts": [
            "test-harness = test_harness.main:cli",
            # Runs one suite against several services in turn, so a single
            # scheduled Job can sweep the ARS and each ARA sequentially.
            "test-harness-sweep = test_harness.sweep:cli",
            # Uploads a run the harness saved instead of sending to the
            # Information Radiator (--local, or the radiator was unreachable).
            "test-harness-radiator = test_harness.radiator_client:cli",
        ],
    },
)
