from enum import Enum
from pydantic import BaseModel
from typing import List, Optional


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

    id: Optional[int]
    type: Type
    env: Env
    query_type: QueryType
    expected_output: ExpectedOutput
    input_curie: str
    output_curie: str


class Tests(BaseModel):
    """List of Test Cases."""

    __root__: List[TestCase]

    def __len__(self):
        return len(self.__root__)

    def __iter__(self):
        return self.__root__.__iter__()

    def __contains__(self, v):
        return self.__root__.__contains__(v)

    def __getitem__(self, i):
        return self.__root__.__getitem__(i)


class TestSuite(BaseModel):
    """
    Test Suite containing the ids of Test Cases.

    id: int
    case_ids: List[int]
    """

    id: int
    case_ids: List[int]


class TestResult(BaseModel):
    """Output of a Test."""

    status: str
