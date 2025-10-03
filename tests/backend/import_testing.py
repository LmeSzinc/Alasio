import builtins
import multiprocessing
import sys
import traceback
from functools import wraps
from typing import Callable, Dict, List, Optional, Set, TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from multiprocessing.connection import PipeConnection


class ImportTracker:
    """Utility class for tracking module import chains"""

    def __init__(self):
        self.import_chain: Dict[str, str] = {}
        self.import_stack: List[str] = []
        self.original_import = builtins.__import__

    def _custom_import(self, name, globals=None, locals=None, fromlist=(), level=0):
        if self.import_stack and name not in self.import_chain:
            self.import_chain[name] = self.import_stack[-1]

        self.import_stack.append(name)
        try:
            return self.original_import(name, globals, locals, fromlist, level)
        finally:
            if self.import_stack and self.import_stack[-1] == name:
                self.import_stack.pop()

    def __enter__(self):
        builtins.__import__ = self._custom_import
        return self

    def __exit__(self, *args):
        builtins.__import__ = self.original_import

    def get_import_path(self, module_name: str) -> List[str]:
        """Get the complete import chain path for a module"""
        path = []
        current = module_name
        visited = set()

        while current and current not in visited:
            path.append(current)
            visited.add(current)
            current = self.import_chain.get(current)

        return list(reversed(path))


class ImportViolation:
    """Represents an import violation"""

    def __init__(self, library: str, modules: List[str], import_chain: List[str]):
        self.library = library
        self.modules = modules
        self.import_chain = import_chain

    @property
    def direct_importer(self) -> str:
        """Get the module that directly imported this library"""
        if len(self.import_chain) >= 2:
            return self.import_chain[-2]
        return "Unknown"

    def to_dict(self) -> dict:
        """Convert to serializable dictionary"""
        return {
            'library': self.library,
            'modules': self.modules,
            'import_chain': self.import_chain,
            'direct_importer': self.direct_importer
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'ImportViolation':
        """Create instance from dictionary"""
        return cls(
            library=data['library'],
            modules=data['modules'],
            import_chain=data['import_chain']
        )


class ForbiddenImportDetector:
    """Detect if forbidden libraries are imported"""

    def __init__(self, forbidden_libraries: Set[str]):
        """
        Initialize the detector

        Args:
            forbidden_libraries: Set of forbidden library names
        """
        self.forbidden_libraries = forbidden_libraries
        self.modules_before = set()

    def record_baseline(self):
        """Record currently imported modules as baseline"""
        self.modules_before = set(sys.modules.keys())

    def detect_violations(self, tracker: ImportTracker) -> List[ImportViolation]:
        """
        Detect if there are any import violations

        Args:
            tracker: ImportTracker instance for getting import chains

        Returns:
            List of violations
        """
        modules_after = set(sys.modules.keys())
        newly_imported = modules_after - self.modules_before

        violations = []
        for forbidden in self.forbidden_libraries:
            forbidden_modules = self._find_forbidden_modules(forbidden, newly_imported)

            if forbidden_modules:
                import_path = tracker.get_import_path(forbidden)
                violations.append(
                    ImportViolation(forbidden, forbidden_modules, import_path)
                )

        return violations

    @staticmethod
    def _find_forbidden_modules(library_name: str, modules: Set[str]) -> List[str]:
        """
        Find matching forbidden libraries and their submodules in the module set

        Args:
            library_name: Library name
            modules: Set of modules to search

        Returns:
            List of matching modules
        """
        return [
            m for m in modules
            if m == library_name or m.startswith(library_name + '.')
        ]


class ImportDiagnosticsReporter:
    """Generate diagnostic reports for import violations"""

    def format_report(self, violations: List[ImportViolation], test_name: str = "") -> str:
        """
        Generate formatted diagnostic report

        Args:
            violations: List of violations
            test_name: Test name

        Returns:
            Formatted report string
        """
        if not violations:
            return ""

        lines = self._format_header(test_name)

        for violation in violations:
            lines.extend(self._format_violation(violation))

        lines.extend(self._format_fix_suggestions())

        return "\n".join(lines)

    @staticmethod
    def _format_header(test_name: str = "") -> List[str]:
        """Format report header"""
        header = [
            "",
            "=" * 80,
        ]
        if test_name:
            header.append(f"❌ [{test_name}] Detected the following heavy libraries imported at application startup:")
        else:
            header.append("❌ Detected the following heavy libraries imported at application startup:")
        header.append("=" * 80)
        return header

    @staticmethod
    def _format_violation(violation: ImportViolation) -> List[str]:
        """
        Format a single violation

        Args:
            violation: Violation object

        Returns:
            List of formatted lines
        """
        lines = [
            "",
            f"📦 Library: {violation.library}",
            f"   Number of imported modules: {len(violation.modules)}",
        ]

        if violation.import_chain:
            lines.extend(ImportDiagnosticsReporter._format_import_chain(violation.import_chain))
        else:
            lines.append("   (Unable to trace complete import chain)")

        if violation.direct_importer != "Unknown":
            lines.extend(ImportDiagnosticsReporter._format_fix_hint(violation))

        return lines

    @staticmethod
    def _format_import_chain(import_chain: List[str]) -> List[str]:
        """
        Format import chain

        Args:
            import_chain: List of import chain

        Returns:
            List of formatted lines
        """
        lines = ["   Import chain:"]
        for i, module in enumerate(import_chain):
            indent = "      " + "  " * i
            arrow = "└─> " if i > 0 else ""
            lines.append(f"{indent}{arrow}{module}")
        return lines

    @staticmethod
    def _format_fix_hint(violation: ImportViolation) -> List[str]:
        """
        Format fix hint

        Args:
            violation: Violation object

        Returns:
            List of formatted lines
        """
        return [
            "",
            f"   🎯 Direct importer: {violation.direct_importer}",
            f"   ⚠️  Suggestion: Change {violation.library} to lazy import in {violation.direct_importer}",
        ]

    @staticmethod
    def _format_fix_suggestions() -> List[str]:
        """Format general fix suggestions"""
        return [
            "=" * 80,
            "💡 How to fix:",
            "   1. Find the module file marked with 🎯 above",
            "   2. Search for 'import xxx' or 'from xxx import' statements in that file",
            "   3. Move the import statement from the top of the file to inside functions (lazy import)",
            "   4. If the dependency must be loaded at startup, consider removing it from the detection list",
            "",
            "Example fix:",
            "   # Before (top-level import, loaded at startup)",
            "   import numpy as np",
            "   def process_data(): ...",
            "",
            "   # After (lazy import, loaded only when used)",
            "   def process_data():",
            "       import numpy as np",
            "       ...",
            "=" * 80,
            "",
        ]

    @staticmethod
    def format_summary(violations: List[ImportViolation]) -> str:
        """
        Generate brief summary for test failure message

        Args:
            violations: List of violations

        Returns:
            Summary string
        """
        summary_parts = []
        for v in violations:
            if v.direct_importer != "Unknown":
                summary_parts.append(f"{v.library} (imported by {v.direct_importer})")
            else:
                summary_parts.append(v.library)

        return f"Found {len(violations)} heavy libraries imported: {', '.join(summary_parts)}"


def _subprocess_import_test_worker(
        conn: "PipeConnection",
        func: Callable,
        forbidden_libraries: Set[str]
):
    """
    Subprocess worker function: Execute import test in isolated environment

    Args:
        conn: Pipe connection for communication
        func: Import function to test
        forbidden_libraries: Set of forbidden libraries
    """
    try:
        # Create detector
        detector = ForbiddenImportDetector(forbidden_libraries)
        detector.record_baseline()

        # Use import tracker
        with ImportTracker() as tracker:
            # Execute user's import function
            func()

            # Detect violations
            violations = detector.detect_violations(tracker)

            # Serialize and send violation information
            violations_data = [v.to_dict() for v in violations]
            conn.send({'success': True, 'violations': violations_data})

    except Exception as e:
        # If error occurs, send error information
        error_info = {
            'success': False,
            'error': str(e),
            'traceback': traceback.format_exc()
        }
        conn.send(error_info)
    finally:
        conn.close()


def run_import_test_isolated(
        import_func: Callable,
        forbidden_libraries: Set[str],
        test_name: str = "",
        timeout: int = 30
) -> Optional[List[ImportViolation]]:
    """
    Run import test in isolated subprocess (core function)

    Uses spawn mode to create subprocess, ensuring cross-platform consistency and complete isolation.
    Communicates between processes via Pipe to pass test results.

    Args:
        import_func: Import function to test (no parameters)
        forbidden_libraries: Set of forbidden library names
        test_name: Test name for error reporting
        timeout: Subprocess timeout in seconds, default 30 seconds

    Returns:
        Returns list of violations if any, otherwise None

    Raises:
        pytest.fail: When violations are detected or execution fails

    Example:
        def my_import():
            from myapp import create_app
            create_app()

        violations = run_import_test_isolated(
            my_import,
            {'numpy', 'pandas'},
            'Main App Test'
        )
        if violations:
            # Handle violations
            pass
    """
    # Get spawn context for cross-platform consistency
    ctx = multiprocessing.get_context('spawn')

    # Create pipe for inter-process communication
    parent_conn, child_conn = ctx.Pipe()

    # Create subprocess
    process = ctx.Process(
        target=_subprocess_import_test_worker,
        args=(child_conn, import_func, forbidden_libraries)
    )

    # Start subprocess
    process.start()

    # Wait for subprocess to complete and get results
    try:
        if parent_conn.poll(timeout):
            result = parent_conn.recv()
        else:
            result = None
    except EOFError:
        result = None

    process.join(timeout=1)

    # Check if timeout occurred
    if process.is_alive():
        process.terminate()
        process.join()
        error_msg = f"Import test timed out ({timeout}s)"
        if test_name:
            error_msg = f"[{test_name}] {error_msg}"
        pytest.fail(error_msg)

    # Check if results were successfully received
    if result is None:
        error_msg = "Subprocess exited abnormally, no results returned"
        if test_name:
            error_msg = f"[{test_name}] {error_msg}"
        pytest.fail(error_msg)

    # Check for errors
    if not result.get('success', False):
        error = result.get('error', 'Unknown error')
        tb = result.get('traceback', '')
        error_msg = f"Import test execution failed:\n{error}\n\n{tb}"
        if test_name:
            error_msg = f"[{test_name}] {error_msg}"
        pytest.fail(error_msg)

    # Deserialize violation information
    violations = [
        ImportViolation.from_dict(v)
        for v in result.get('violations', [])
    ]

    return violations if violations else None


class HeavyImportTest:
    def __init__(self, forbidden_libraries: Set[str], test_name: str = "", timeout: int = 30):
        """
        Initialize test instance

        Args:
            forbidden_libraries: Set of forbidden library names
            test_name: Test name to display in reports
            timeout: Subprocess timeout in seconds, default 30 seconds
        """
        self.forbidden_libraries = forbidden_libraries
        self.test_name = test_name
        self.timeout = timeout
        self.reporter = ImportDiagnosticsReporter()

    def run_test(self, import_callback: Callable):
        """
        Run import test in isolated subprocess

        Args:
            import_callback: A function with no parameters that executes the import operations to test

        Raises:
            pytest.fail: When violations are detected or execution fails

        Example:
            # note that a spawned subprocess requires module-level function, you must define a standalone function here.
            # function with decorator, function inside a function, class method, can't be pickled and run.
            def import_backend():
                from alasio.backend.app import create_app
                create_app()

            def test_backend_no_heavy_import():
                run = HeavyImportTest({
                    # no image processing
                    'numpy', 'scipy', 'cv2', 'av', 'matplotlib',
                })
                run.run_test(import_backend)
        """
        # Call core test function, execute in subprocess
        violations = run_import_test_isolated(
            import_callback,
            self.forbidden_libraries,
            self.test_name,
            self.timeout
        )

        # If violations exist, generate report and fail test
        if violations:
            print(self.reporter.format_report(violations, self.test_name))
            pytest.fail(self.reporter.format_summary(violations))
