"""
Postprocess registration info and execution.

A postprocess function is validated when it is registered, its call plan is
cached in PostprocessInfo, and run_postprocess() only uses the cached plan.
"""

import inspect
from typing import Any

import msgspec

from alasio.ext.cache import cached_property

# Supported call styles of PostprocessInfo.plan
_CALL_STYLES = {'data', 'data_kw', 'cls_data', 'cls_data_kw'}


def _definition_error(msg):
    """
    Build a DefinitionError without a module level import.

    base.py imports PostprocessInfo from this module, so importing
    DefinitionError from base at module level would be circular.

    Args:
        msg (str): Error message

    Returns:
        DefinitionError: Error instance
    """
    from alasio.config_dev.parse.base import DefinitionError
    return DefinitionError(msg)


def _postprocess_name(postprocess):
    """
    Get a stable name of a postprocess function for error messages.

    Args:
        postprocess (callable): Postprocess function or callable object

    Returns:
        str: Function name, or class name for a callable object
    """
    name = getattr(postprocess, '__name__', None)
    if name is None:
        name = type(postprocess).__name__
    return name


def _postprocess_signature(postprocess):
    """
    Get the signature of a postprocess function for error messages.

    Args:
        postprocess (callable): Postprocess function or callable object

    Returns:
        str: Signature, or "<unknown>" if it can not be inspected
    """
    try:
        return str(inspect.signature(postprocess))
    except (TypeError, ValueError):
        return '<unknown>'


def _get_postprocess_plan(postprocess):
    """
    Determine how a postprocess function wants to receive the data.

    The shape is inferred from the required parameters:

        - one required positional -> ``postprocess(data)``
        - two required positional -> ``postprocess(cls, data)``
        - one required keyword-only and no required positional ->
          ``postprocess(**{name: data})``
        - one required positional + required keyword-only "data" ->
          ``postprocess(cls, data=data)``

    Optional parameters, ``*args`` and ``**kwargs`` do not change the shape.

    Args:
        postprocess (callable): Postprocess function or callable object

    Returns:
        tuple[str, str] | None:
            ("data", "") to call postprocess(data)
            ("data_kw", name) to call postprocess(**{name: data})
            ("cls_data", "") to call postprocess(cls, data)
            ("cls_data_kw", name) to call postprocess(cls, **{name: data})
            None if the signature is not supported
    """
    try:
        sig = inspect.signature(postprocess)
    except (TypeError, ValueError):
        # A C callable or another object without an inspectable signature,
        # keep the historical behavior and call it with the data only.
        return ('data', '')

    params = list(sig.parameters.values())
    positional = [
        p for p in params
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    required_pos = [p for p in positional if p.default is p.empty]
    required_kw = [
        p for p in params
        if p.kind is p.KEYWORD_ONLY and p.default is p.empty
    ]
    has_var_positional = any(p.kind is p.VAR_POSITIONAL for p in params)
    has_var_keyword = any(p.kind is p.VAR_KEYWORD for p in params)

    if not required_pos and not required_kw:
        # No required argument, data goes to a positional slot or **kwargs
        if positional or has_var_positional:
            return ('data', '')
        if has_var_keyword:
            return ('data_kw', 'data')
        return None
    if len(required_pos) == 1 and not required_kw:
        return ('data', '')
    if len(required_pos) == 2 and not required_kw:
        return ('cls_data', '')
    if not required_pos and len(required_kw) == 1:
        # The only required argument is the data argument, use its name
        return ('data_kw', required_kw[0].name)
    if len(required_pos) == 1 and len(required_kw) == 1 and required_kw[0].name == 'data':
        return ('cls_data_kw', 'data')
    return None


def _resolve_postprocess_cls(postprocess):
    """
    Resolve the class owning an unbound postprocess function.

    The postprocess decorator runs while the class body is still executing, so
    the class does not exist yet. It is resolved lazily from __qualname__.

    Args:
        postprocess (callable): Unbound postprocess function

    Returns:
        type | None: Owning class, or None if it can not be resolved
    """
    qualname = getattr(postprocess, '__qualname__', '')
    parts = qualname.split('.')
    if len(parts) < 2:
        return None
    container = getattr(postprocess, '__globals__', None)
    for part in parts[:-1]:
        if isinstance(container, dict):
            container = container.get(part)
        else:
            container = getattr(container, part, None)
        if container is None:
            return None
    return container if isinstance(container, type) else None


def _call_postprocess(postprocess, data, style, keyword):
    """
    Call a postprocess function with the data argument.

    Args:
        postprocess (callable): Postprocess function
        data (dict): Data to process
        style (str): "data" or "data_kw"
        keyword (str): Keyword name of the data argument for "data_kw"

    Returns:
        Any: Processed data
    """
    if style == 'data':
        return postprocess(data)
    return postprocess(**{keyword: data})


class PostprocessInfo(msgspec.Struct, dict=True):
    # postprocess function or callable object
    func: Any

    @cached_property
    def name(self):
        """
        Get a stable name of the postprocess for error messages.

        Returns:
            str: Function name, or class name for a callable object
        """
        return _postprocess_name(self.func)

    @cached_property
    def signature(self):
        """
        Get the signature of the postprocess function for error messages.

        Returns:
            str: Signature, or "<unknown>" if it can not be inspected
        """
        return _postprocess_signature(self.func)

    @cached_property
    def plan(self):
        """
        Build and validate the call plan from the function signature.

        Returns:
            tuple[str, str]: Call plan of the function

        Raises:
            DefinitionError: Unsupported signature, or a (cls, data) function
                whose class can never be resolved from __qualname__
        """
        plan = _get_postprocess_plan(self.func)
        if plan is None:
            raise _definition_error(
                f'Invalid postprocess signature for "{self.name}", '
                f'must accept (data) or (cls, data), got {self.signature}')
        if not plan[0].startswith('cls_'):
            return plan

        if not inspect.isfunction(self.func):
            raise _definition_error(
                f'Postprocess function "{self.name}" is not a plain function, '
                f'it can not receive a resolved class, use a 1-arg (data) function')
        qualname = getattr(self.func, '__qualname__', '')
        parts = qualname.split('.')
        if len(parts) < 2 or '<locals>' in parts:
            raise _definition_error(
                f'Cannot resolve the class of postprocess function '
                f'"{self.name}" from __qualname__ "{qualname}", '
                f'define it in a class body or use a 1-arg (data) function')
        return plan

    @classmethod
    def from_function(cls, func):
        """
        Build PostprocessInfo and prewarm its cached name / signature / plan.

        The plan is inferred from the function signature and cached in the
        returned object, so an invalid signature fails here instead of at call
        time.

        Args:
            func (callable): Postprocess function or callable object

        Returns:
            PostprocessInfo: Registered postprocess info

        Raises:
            DefinitionError: Unsupported signature, or a (cls, data) function
                whose class can never be resolved from __qualname__
        """
        info = cls(func=func)
        # Prewarm cached values, plan validation raises on an invalid function
        _ = info.name
        _ = info.signature
        _ = info.plan
        return info


def run_postprocess(info, data):
    """
    Run a registered postprocess with its cached call plan.

    Args:
        info (PostprocessInfo): Registered postprocess info
        data (dict): Data to process

    Returns:
        Any: Processed data

    Raises:
        DefinitionError: The info has no call plan, a (cls, data) function
            can not resolve its class from __qualname__, or the postprocess
            returned None
    """
    plan = info.plan
    if not plan:
        raise _definition_error(
            f'Postprocess function "{info.name}" has no call plan, '
            f'build it with PostprocessInfo.from_function()')
    if len(plan) != 2:
        raise _definition_error(
            f'Invalid postprocess plan for "{info.name}": {plan!r}')
    style, keyword = plan
    if style not in _CALL_STYLES:
        raise _definition_error(
            f'Invalid postprocess plan for "{info.name}": {plan!r}')
    if style.endswith('_kw') and not keyword:
        raise _definition_error(
            f'Invalid postprocess plan for "{info.name}": {plan!r}')

    func = info.func
    # A bound method or classmethod already carries its owner, it only receives
    # the data.
    if hasattr(func, '__self__'):
        if style not in ('data', 'data_kw'):
            raise _definition_error(
                f'Postprocess function "{info.name}" is already bound, '
                f'it must accept (data), got {info.signature}')
        result = _call_postprocess(func, data, style, keyword)
    elif style in ('data', 'data_kw'):
        result = _call_postprocess(func, data, style, keyword)
    else:
        cls = _resolve_postprocess_cls(func)
        if cls is None:
            raise _definition_error(
                f'Cannot resolve the class of postprocess function "{info.name}" '
                f'from __qualname__ "{getattr(func, "__qualname__", "")}", '
                f'define it in a class body or use a 1-arg (data) function')
        if style == 'cls_data':
            result = func(cls, data)
        else:
            result = func(cls, **{keyword: data})

    if result is None:
        raise _definition_error(
            f'Postprocess function "{info.name}" returned None, '
            f'did you forget "return data"?')
    return result
