"""
Tests for the heavy import detector of the backend startup.

The detector reports a forbidden library as a violation. A library the backend
is built on may import it, e.g. starlette imports asyncio for its async
callable check before python 3.13: the importer decides, not only the library.
"""
from tests.backend.import_testing import ForbiddenImportDetector, ImportDiagnosticsReporter, ImportViolation


def violation(library, importer):
    """
    Build a violation whose direct importer is the given module.

    Args:
        library (str): The imported library
        importer (str): The module that imported it

    Returns:
        ImportViolation:
    """
    return ImportViolation(library, [library], ['tests.backend.test_import', importer, library])


class TestAllowedImporters:
    """A forbidden library is accepted when an allowed module imports it."""

    def test_without_an_allow_list(self):
        """Every importer is a violation without an allow-list."""
        detector = ForbiddenImportDetector({'asyncio'})
        assert detector._is_allowed(violation('asyncio', 'starlette._utils')) is False

    def test_allowed_importer_and_its_submodules(self):
        """A name allows the module itself and its submodules."""
        detector = ForbiddenImportDetector({'asyncio'}, {'asyncio': {'starlette'}})
        assert detector._is_allowed(violation('asyncio', 'starlette')) is True
        assert detector._is_allowed(violation('asyncio', 'starlette._utils')) is True

    def test_other_importers(self):
        """A module of the project is never covered by the allow-list."""
        detector = ForbiddenImportDetector({'asyncio'}, {'asyncio': {'starlette'}})
        assert detector._is_allowed(violation('asyncio', 'alasio.backend.app.app')) is False
        # a name that only starts with the same characters is not the module tree
        assert detector._is_allowed(violation('asyncio', 'starlette_fork')) is False

    def test_other_libraries(self):
        """The allow-list of one library does not cover another library."""
        detector = ForbiddenImportDetector({'asyncio', 'greenlet'}, {'asyncio': {'starlette'}})
        assert detector._is_allowed(violation('greenlet', 'starlette._utils')) is False


class TestReport:
    """The report is printed to the raw console, it has to be ASCII."""

    def test_the_report_is_ascii(self):
        """A console of a non utf-8 code page can print the report."""
        report = ImportDiagnosticsReporter().format_report(
            [violation('anyio', 'starlette.responses')], 'Backend process')
        assert report.isascii()
