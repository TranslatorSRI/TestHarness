"""Setup file for SRI Test Harness package."""

from setuptools import setup

with open("README.md", encoding="utf-8") as readme_file:
    readme = readme_file.read()

setup(
    name="sri-test-harness",
    version="0.4.0",
    author="Max Wang",
    author_email="max@covar.com",
    url="https://github.com/TranslatorSRI/TestHarness",
    description="Translator SRI Test Harness",
    long_description_content_type="text/markdown",
    long_description=readme,
    packages=["test_harness"],
    include_package_data=True,
    zip_safe=False,
    license="MIT",
    python_requires=">=3.9",
    entry_points={
        "console_scripts": [
            "test-harness = test_harness.main:cli",
        ],
    },
)
