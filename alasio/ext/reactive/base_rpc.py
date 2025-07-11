import inspect

from msgspec import ValidationError, convert

SIG_EMPTY = inspect.Signature.empty


class RPCMethod:
    def __init__(self, func):
        """
        Args:
            func (callable):
        """
        self.func = func
        # key: arg name (without "self"), value: arg default,
        # if arg has no default, value is SIG_EMPTY
        self.dict_default = {}
        # key: arg name (without "self"), value: arg annotation,
        # if arg has no default, value is SIG_EMPTY
        self.dict_annotation = {}

        self.build()

    def build(self):
        """
        Build func arg info
        """
        dict_default = self.dict_default
        dict_annotation = self.dict_annotation

        sig = inspect.signature(self.func)
        for name, arg in sig.parameters.items():
            if name == 'self':
                continue
            dict_default[name] = arg.default
            dict_annotation[name] = arg.annotation

    def convert(self, event_value):
        """
        Convert value (RequestEvent.v) to function input

        Returns:
            dict[str, Any]:

        Raises:
            ValidationError:
        """
        kwargs = {}
        dict_annotation = self.dict_annotation
        for name, default in self.dict_default.items():
            # try to get arg from input
            try:
                value = event_value.get(name, default)
            except Exception:
                # event_value is not a dict
                raise ValidationError(f'Input is not a dict')

            # Missing arg and arg has no default
            if value is SIG_EMPTY:
                raise ValidationError(f'Missing arg: "{name}"')

            # try to convert arg
            anno = dict_annotation.get(SIG_EMPTY)
            if anno is not SIG_EMPTY:
                try:
                    value = convert(value, anno)
                except ValidationError as e:
                    raise ValidationError(f'Invalid type for arg "{name}": {e}')

            # all good
            kwargs[name] = value

        return kwargs

    def call_sync(self, instance, event_value):
        """
        Args:
            instance (BaseTopic):
            event_value:
        """
        kwargs = self.convert(event_value)
        return self.func(instance, **kwargs)

    async def call_async(self, instance, event_value):
        """
        Args:
            instance (BaseTopic):
            event_value:
        """
        kwargs = self.convert(event_value)
        return await self.func(instance, **kwargs)


def rpc(func):
    """
    A decorator that transforms a method into an RPCMethod object and marks it for registration.

    Note that, if an RPC method success, success response will be sent.
    If an RPC method raises error, error will be converted to error message and error response will be sent.

    Examples:
        class ConfigScan(BaseTopic):
            @rpc
            async def config_add(self, name: str, mod: str):
                pass
    """
    # Instead of just setting a flag, we wrap it in an RPCMethod instance immediately.
    # This pre-calculates the parameter processors.
    method = RPCMethod(func)

    # Store the instance on a special attribute of the original function.
    # The metaclass or __init_subclass__ will find this.
    func._rpc_method_instance = method
    return func
