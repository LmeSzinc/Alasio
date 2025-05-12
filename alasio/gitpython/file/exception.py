class ObjectBroken(Exception):
    # Raised when object data is truncated
    def __init__(self, reason, data=None):
        """
        Args:
            reason (str):
            data (memoryview | bytes):
        """
        self.reason = reason
        self.data = data
        super().__init__(self.reason)

    def __str__(self):
        if self.data is not None:
            # pretty show bytes
            data = self.data
            if len(data) > 50:
                data = data[:50]
                if isinstance(data, memoryview):
                    data = data.tobytes()
                data = data.hex() + f'...(size={len(self.data)})'
            else:
                if isinstance(data, memoryview):
                    data = data.tobytes()
                data = data.hex()
            return f"ObjectBroken: {self.reason}\n{data}"
        else:
            return f"ObjectBroken: {self.reason}"


class PackBroken(Exception):
    pass


def convert_sha1(sha1):
    """
    Convert a user-input sha1 to string format

    Args:
        sha1 (bytes | str): Can in the following format
            'ABCDE12345ABCDE12345ABCDE12345ABCDE12345' hex in string of length 40
            b'ABCDE12345ABCDE12345ABCDE12345ABCDE12345' hex in bytes of length 40
            b'\x01\x02\x03\x04\x05\x01\x02\x03\x04\x05\x01\x02\x03\x04\x05\x01\x02\x03\x04\x05' bytes in length 20

    Returns:
        bytes: sha1 in length 40, 'ABCDE12345ABCDE12345ABCDE12345ABCDE12345'
    """
    if isinstance(sha1, bytes):
        length = len(sha1)
        if length == 20:
            # bytes in length 20
            return sha1.hex()
        elif length == 40:
            # hex in bytes of length 40
            # may raise ValueError
            return sha1.decode()
        else:
            raise ValueError(f'Unexpected sha1 length: {sha1}')
    elif isinstance(sha1, str):
        if len(sha1) == 40:
            # expected
            return sha1
        else:
            raise ValueError(f'Unexpected sha1 length: {sha1}')
    else:
        raise ValueError(f'Unexpected sha1 type: {sha1}')
