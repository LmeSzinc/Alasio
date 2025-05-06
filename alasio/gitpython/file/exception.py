class ObjectBroken(Exception):
    # Raised when object data is truncated
    def __init__(self, reason, data=None):
        """
        Args:
            reason (str):
            data (bytes):
        """
        self.reason = reason
        self.data = data
        super().__init__(self.reason)

    def __str__(self):
        if self.data is not None:
            return f"ObjectBroken: {self.reason}\n{self.data}"
        return f"ObjectBroken: {self.reason}"
