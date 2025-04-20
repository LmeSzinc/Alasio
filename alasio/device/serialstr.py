


class SerialStr(str):
    """
    Extends str with serial helper functions
    """
    @classmethod
    def revise_serial(cls, serial: str) -> "SerialStr":
        """
        Tons of fool-proof fixes to handle manual serial input
        To load a serial:
            serial = SerialStr.revise_serial(serial)
        """
        serial = serial.replace(' ', '')
        # 127。0。0。1：5555
        serial = serial.replace('。', '.').replace('，', '.').replace(',', '.').replace('：', ':')
        # 127.0.0.1.5555
        serial = serial.replace('127.0.0.1.', '127.0.0.1:')
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
