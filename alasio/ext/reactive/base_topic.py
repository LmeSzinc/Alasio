from .base_rpc import RPCMethod


class BaseTopic:
    # subclasses should override `topic` and topic name should be unique
    # If topic name is empty, class name will be used
    # The following names are preserved:
    # - "error", the builtin topic to give response to invalid input
    name = ''
    # a collection of RPC methods
    rpc_methods: "dict[str, RPCMethod]" = {}

    @classmethod
    def topic_name(cls):
        if cls.name:
            return cls.name
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
        cls._rpc_methods_ = cls.rpc_methods.copy()

        for name, member in cls.__dict__.items():
            if callable(member) and hasattr(member, '_rpc_method_instance'):
                # The decorator has already done the heavy lifting. We just collect the result.
                cls.rpc_methods[name] = member._rpc_method_instance
