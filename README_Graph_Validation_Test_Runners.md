# Graph Validation Test Runners

See [Graph Validation Test Runners GitHub repository](https://github.com/TranslatorSRI/graph-validation-test-runners) for more details.

## Overview

The two Graph Validation TestRunners are the (TRAPI/Biolink compliance) "Standards Validation" and "One Hop" TestRunner. These two TestRunners have comparable test inputs and configuration, except in how they are targeted for execution. Their specific functionality are

1. **Standards Validation TestRunner:** uses the "reasoner-validator" package to validate
TRAPI and Biolink Model compliance of inputs and outputs templated TRAPI queries.

2. **One Hop TestRunner:** is a bit different from other types of Translator tests. Generally, a single OneHopTest TestAsset is single S-P-O triplet with categories, used internally to generate a half dozen distinct TestCases and a single KP or ARA TRAPI service is called several times, once for each generated TestCase.

There is no sense of "ExpectedOutput" in the tests rather, test pass, fail or skip status is an intrinsic
outcome pertaining to the recovery of the input test asset values in the output of the various TestCases.
A list of such TestAssets run against a given KP or ARA target service, could be deemed a "TestSuite".
But a set of such TestSuites could be run in batch within a given TestSession. It is somewhat hard
to align with this framework to the new Translator Test Harness, or at least, not as efficient to run.

To make this work, we do stretch the design of the testing model a bit as a data transfer object by wrapping each input S-P-O triple as a single TestCase, extract a single associated TestAsset, which we'll feed in with the value of the TestCase 'components' field value, which will be taken as the 'infores' of the ARA or KP to be tested.

Internally, we'll generate and run TRAPI queries of the actual TestCase instances against the 'infores' specified resources, then return the results, suitably indexed.  Alternately, if the specified target is the 'ars', then the returned results will be indexed by 'pks'(?)

## Additional Parameters

In principle, additional (optional) keyword arguments could be given in the current 
the 'test_inputs' as a means to configure the BiolinkValidator class in reasoner-validator
with additional parameters like 'target_provenance' and 'strict_validation'; however,
it is unclear at this moment where and how these can or should be specified.
**kwargs