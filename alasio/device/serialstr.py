from typing_extensions import Self


class SerialStr(str):
    """
    Extends str with serial helper functions
    """

    @classmethod
    def revise_serial(cls, serial: str) -> Self:
        """
        Tons of fool-proof fixes to handle manual serial input
        To load a serial:
            serial = SerialStr.revise_serial(serial)
        """
        serial = serial.strip().replace(' ', '')
        # 127。0。0。1：5555
        serial = serial.replace('。', '.').replace('，', '.').replace(',', '.').replace('：', ':')
        # 127.0.0.1.5555
        serial = serial.replace('127.0.0.1.', '127.0.0.1:')
        # 5555,16384 (actually "5555.16384" because replace(',', '.'))
        if '.' in serial:
            left, _, right = serial.partition('.')
            try:
                left = int(left)
                right = int(right)
                if 5500 < left < 6000 and 16300 < right < 20000:
                    serial = str(right)
            except ValueError:
                pass
        # 16384
        if serial.isdigit():
            try:
                port = int(serial)
                if 1000 < port < 65536:
                    serial = f'127.0.0.1:{port}'
            except ValueError:
                pass
        # 夜神模拟器 127.0.0.1:62001
        # MuMu模拟器12127.0.0.1:16384
        if '模拟' in serial:
            import re
            res = re.search(r'(127\.\d+\.\d+\.\d+:\d+)', serial)
            if res:
                serial = res.group(1)
        # 12127.0.0.1:16384
        serial = serial.replace('12127.0.0.1', '127.0.0.1')
        # auto127.0.0.1:16384
        serial = serial.replace('auto127.0.0.1', '127.0.0.1').replace('autoemulator', 'emulator')
        return cls(serial)

    @staticmethod
    def get_serial_pair(serial):
        """
        Args:
            serial (str):

        Returns:
            tuple[Optional[str], Optional[str]]: `127.0.0.1:5555+{X}` and `emulator-5554+{X}`, 0 <= X <= 32
        """
        if serial.startswith('127.0.0.1:'):
            try:
                port = int(serial[10:])
                if 5555 <= port <= 5555 + 64:
                    return f'127.0.0.1:{port}', f'emulator-{port - 1}'
            except (ValueError, IndexError):
                pass
        if serial.startswith('emulator-'):
            try:
                port = int(serial[9:])
                if 5554 <= port <= 5554 + 64:
                    return f'127.0.0.1:{port + 1}', f'emulator-{port}'
            except (ValueError, IndexError):
                pass

        return None, None
