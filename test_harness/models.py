from enum import Enum
from pydantic import BaseModel


class Type(str, Enum):
    acceptance = "acceptance"
    quantitative = "quantitative"


class Env(str, Enum):
    dev = "dev"
    ci = "ci"
    test = "test"
    prod = "prod"


class QueryType(str, Enum):
    treats = "treats(creative)"


class ExpectedOutput(str, Enum):
    top_answer = "TopAnswer"
    acceptable = "Acceptable"
    bad_but_forgivable = "BadButForgivable"
    never_show = "NeverShow"


class TestCase(BaseModel):
    """
    Test Case that Test Runners can ingest.

    type: Type
    env: Env
    query_type: QueryType
    expected_output: ExpectedOutput
    input_curie: str
    output_curie: str
    """

    type: Type
    env: Env
    query_type: QueryType
    expected_output: ExpectedOutput
    input_curie: str
    output_curie: str


class TestResult(BaseModel):
    """Output of a Test."""

    status: str
