"""Example tests for the Test Harness."""

example_tests = [
    {
        "type": "acceptance",
        "env": "test",
        "query_type": "treats(creative)",
        "expected_output": "TopAnswer",
        "input_curie": "MONDO:0015564",
        "output_curie": "PUBCHEM.COMPOUND:5284616",
    }
]
