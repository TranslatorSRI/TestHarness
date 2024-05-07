# Microsoft Windows Development and Running

The TestHarness is written in Python, which is largely Microsoft Windows compatible; however, specific libraries may be pulled into the system which use software libraries with a lower level of Microsoft Windows compatibility. Workarounds for these are discussed here.

## jq library

'jq' is a lightweight and flexible JSON processor used by the user interface TestRunners. Unfortunately, Python wheels are mainly only available for Linux and Mac OS X, which will cause a pip installation error. Potential workarounds are suggested (see https://github.com/mwilliamson/jq.py/issues/20#issuecomment-1411157736) pointing to built Windows wheels, at https://jeffreyknockel.com/jq/). The main challenge is that such wheels as are availablemay not yet be available for required jq releases (i.e. 1.6.0) except for Python releases not (yet) workable for the TestHarness (e.g. 3.12 still has some bugs as of April 25, 2024).  Some build guidelines are available (see https://github.com/jknockel/jq.py/tree/mingw-build).

Once available, the appropriate wheel likely needs to be directly installed from a local directory, e.g. something like 

```
pip install ./dist/jq-1.6.0-cp312-cp312-win_amd64.whl
```
