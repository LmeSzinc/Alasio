"""
Compile the bytecode cache of one source file, best effort.

py_compile.compile() is the only supported way to write the same bytes the
import machinery would write: it packs the header and the marshalled code
object with the interpreter running the call, so the cache tag, the magic
number and the optimization level are the ones of the worker that will read
the file afterwards. What it does not do is survive a failure:

    - doraise=False only covers a compilation error, which it prints to
      stderr and swallows: the caller can not tell a written file from a
      syntax error, so the caller asks for doraise=True and reads the
      exception, which stringify_event() formats as one line
    - the write itself is not protected at all, importlib._bootstrap_external
      writes the file under a temporary name and replaces it with
      os.replace(), which raises on Windows when the cache file is open
      elsewhere, e.g. a worker importing the module at that very moment

A failure is not an error of the warmup: the pyc file of the former run is
still there and python compiles the source again on the next import, which
is exactly what a release without a warmup does. The caller counts the
failure and moves on.

stringify_event() comes from the logger, which the warmup subprocess loads
anyway: the thread pool of the runner imports it. The message of a failure
has to name the class of the exception, an OSError on a file held open and
a PyCompileError on a source of another python version are the same line
of the report otherwise.
"""
import py_compile

from alasio.deploy.simple_pip.pyc import pyc_path
from alasio.logger.utils import stringify_event


def compile_file(py_file):
    """
    Compile one source file into its bytecode cache, best effort.

    The destination is the cache of the source for the running interpreter,
    see pyc_path(): the pyc files of the other interpreters and of the
    manual optimizations are never touched.

    Args:
        py_file (str): Path of the .py source file

    Returns:
        str: Why the compilation failed, '' when the pyc file was written
    """
    try:
        py_compile.compile(py_file, cfile=pyc_path(py_file), doraise=True)
    except Exception as e:
        # The class of the exception tells the kind of failure: a
        # PyCompileError on a source python can not compile, e.g. one of
        # another python version, and an OSError on a write that failed,
        # e.g. a sharing violation on Windows when a worker imports the
        # module at that moment. Python logs the same error when it
        # imports the module
        return stringify_event(e)
    return ''
