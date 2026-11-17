"""
Raised when a dependency file cannot be compiled into a lock.
"""

__all__ = ['CompileError']


class CompileError(ValueError):
    """
    Raised when the dependencies of a project cannot be compiled.

    The message names the distribution, the constraint and the parent that
    required it, so the author can fix the declaration rather than read a
    traceback: no version is picked silently, and no pin is written on failure.
    """
