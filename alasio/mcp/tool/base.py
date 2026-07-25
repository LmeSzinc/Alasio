"""
Base abstractions for MCP tools.

Each tool is a subclass of ToolBase with a typed params model and a typed
result model.  RequestModel defines the wire format for the server.
"""

from typing import Any, Dict, Literal

import msgspec

# ── Wire models ──────────────────────────────────────────────────────────


class RequestModel(msgspec.Struct):
    """Incoming request envelope, validated by the server.

    ``method`` is constrained to the literal set of known tools.
    ``timeout`` controls the per-request deadline (seconds).
    """

    method: Literal[
        "exec_shell",
        "exec_python",
    ]
    params: Dict[str, Any] = msgspec.field(default_factory=dict)
    timeout: int = 20


REQUEST_DECODER = msgspec.json.Decoder(RequestModel)
RESPONSE_ENCODER = msgspec.json.Encoder()


class ResponseModel(msgspec.Struct, omit_defaults=True):
    """Response envelope — exactly one of ``result`` or ``error`` is set.

    Fields default to ``""`` and are omitted from serialization when empty,
    so the wire format is always ``{"result": ...}`` or ``{"error": ...}``.
    """

    result: str = ""
    error: str = ""


# ── Tool base class ──────────────────────────────────────────────────────


class ToolBase:
    """Abstract tool definition.

    Subclasses set ``name`` and implement ``execute()``.  The params dict
    is validated via ``validate()`` before being passed to ``execute()``.
    """

    name: str = ""
    params_model: "type[msgspec.Struct]" = msgspec.Struct
    result_model: "type[msgspec.Struct]" = msgspec.Struct

    def validate(self, params: dict):
        """Convert a raw dict into the typed params model.

        Raises ``msgspec.ValidationError`` on invalid input.
        """
        return msgspec.convert(params, self.params_model)

    def execute(self, params, request):
        """Run the tool with already-validated params.

        Args:
            params: Validated params model instance.
            request (RequestModel): The full decoded request (provides
                ``timeout``, ``method``, etc.).

        Returns:
            A ``result_model`` instance or a plain dict.
        """
        raise NotImplementedError

    def run(self, request):
        """validate + execute wrapper.

        Args:
            request (RequestModel): The decoded request envelope.
        """
        validated = self.validate(request.params)
        return self.execute(validated, request=request)
