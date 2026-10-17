"""
Environment of the screenshot golden tests of the example mod.

The harness plugin is enabled by the pytest configuration of the repository
(pyproject.toml), so the tests only import the `image` decorator and annotate
the parameters they need, see alasio.testing.screenshot.

The example mod is nested in the Alasio repository, so its root has to go on
sys.path for the "module.xxx" imports of the mod. A mod that is a repository of
its own has module/, tasks/ and tests/ at the project root, the root pytest runs
from, and needs no path setup here.
"""
import os
import sys

MOD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if MOD_ROOT not in sys.path:
    sys.path.insert(0, MOD_ROOT)
