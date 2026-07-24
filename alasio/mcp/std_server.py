"""
Fast shell execution service over stdin/stdout.

Protocol: one JSON request per line in, one response per line out.

  Request:  {"method": "exec_python", "params": {"code": "print(1)"}, "timeout": 20}
  Response: {"result": {"stdout": "1\\n", "stderr": ""}}

Commands:
  exec_shell   — Execute a shell command (via subprocess)
  exec_python  — Execute inline Python code (in-process, no subprocess)
"""

import importlib
import sys

from alasio.ext.inflect import Inflection
from alasio.logger.utils import stringify_event
from alasio.mcp.tool.base import REQUEST_DECODER, RESPONSE_ENCODER, ResponseModel, ToolBase

_TOOL_CACHE: "dict[str, ToolBase]" = {}


class StdServer:
    """MCP server over stdin/stdout, dispatch-based protocol.

    Parameters can be injected for testing::

        import io
        stdin = io.StringIO('{"method": ...}\\n')
        stdout = io.StringIO()
        server = StdServer(stdin=stdin, stdout=stdout)
        server.serve()
    """

    def __init__(self, stdin=sys.stdin, stdout=sys.stdout):
        self.stdin = stdin
        self.stdout = stdout

    @staticmethod
    def _get_tool(method):
        """Dynamically import and cache a tool by method name.

        The module is loaded from ``alasio.mcp.tool.{method}`` and the class
        name is derived via ``Inflection.from_string(method).to_pascal_case()``
        (e.g. ``exec_shell`` → ``ExecShell``).
        """
        if method in _TOOL_CACHE:
            return _TOOL_CACHE[method]

        module = importlib.import_module(f"alasio.mcp.tool.{method}")
        class_name = Inflection.from_string(method).to_pascal_case()
        tool_cls = getattr(module, class_name)
        tool = tool_cls()
        _TOOL_CACHE[method] = tool
        return tool

    def _encode_response(self, resp):
        """Serialize a ``ResponseModel`` with msgspec and write it to stdout."""
        line = RESPONSE_ENCODER.encode(resp).decode("utf-8") + "\n"
        self.stdout.write(line)
        self.stdout.flush()

    def _dispatch(self, req):
        """Dispatch a ``RequestModel`` to the matching tool and return a ``ResponseModel``."""
        tool = self._get_tool(req.method)
        if tool is None:
            return ResponseModel(error=f"Unknown method: {req.method}")

        try:
            result = tool.run(req)
        except Exception as e:
            return ResponseModel(error=stringify_event(e))

        # Tool result is a Struct (e.g. ShellResult); encode it to a JSON
        # string so it can be carried inside the ``ResponseModel.result`` field.
        return ResponseModel(
            result=RESPONSE_ENCODER.encode(result).decode("utf-8")
        )

    def serve(self):
        """Main loop: read requests from stdin, dispatch, write responses to stdout."""
        for line in self.stdin:
            line = line.strip()
            if not line:
                self._encode_response(ResponseModel(error="Empty input"))
                continue

            try:
                req = REQUEST_DECODER.decode(line.encode("utf-8"))
            except Exception as e:
                self._encode_response(ResponseModel(error=stringify_event(e)))
                continue

            resp = self._dispatch(req)
            self._encode_response(resp)


def main():
    """Entry point: create a StdServer with the default stdin/stdout and run it."""
    server = StdServer()
    server.serve()


if __name__ == "__main__":
    main()
