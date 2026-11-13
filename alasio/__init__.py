"""
Alasio, a mobile game bot framework.

The version is defined here instead of pyproject.toml: the project declares
``dynamic = ["version"]`` and reads the version with
``[tool.setuptools.dynamic] version = {attr = "alasio.__version__"}``, the
rule of setuptools, so a release bumps the version in one place only.

Keep the assignments plain literals. The builds read this file by parsing it,
never by importing the package, and a computed value can not be read
statically: the wheel builder reads the git commit instead of running it.
"""
__version__ = "2.0.2"
__version_tuple__ = (2, 0, 2)
