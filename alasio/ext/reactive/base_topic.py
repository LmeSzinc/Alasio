from .base_rpc import RPCMethod


class BaseTopic:
    # subclasses should override `topic` and topic name should be unique
    # If topic name is empty, class name will be used
    # The following names are preserved:
    # - "error", the builtin topic to give response to invalid input
    NAME = ''
    # A collection of RPC methods
    # Note that this is auto generated and should be static, don't modify it at runtime
    rpc_methods: "dict[str, RPCMethod]" = {}
    # Whether this topic update with fill event only
    FULL_EVENT_ONLY = False

    @classmethod
    def topic_name(cls):
        if cls.NAME:
            return cls.NAME
        else:
            return cls.__name__

    def __init_subclass__(cls, **kwargs):
        """
        This hook is called when a class inherits from BaseTopic.
        It collects all methods decorated with @rpc, which have been
        pre-processed into RPCMethod objects by the decorator.
        """
        super().__init_subclass__(**kwargs)

        # Create a new registry for this specific subclass, inheriting from parent
        # This prevents child classes from modifying the parent's registry.
        cls.rpc_methods = {}

        for base in cls.__mro__:
            # stop at self
            if base is BaseTopic:
                break
            for name, member in base.__dict__.items():
                if callable(member) and hasattr(member, '_rpc_method_instance'):
                    # The decorator has already done the heavy lifting. We just collect the result.
                    cls.rpc_methods[name] = member._rpc_method_instance
