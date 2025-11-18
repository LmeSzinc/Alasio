from alasio.adb.protocol.const import ShellResult
from alasio.adb.protocol.tcp_protocol import AdbProtocolTCP


class AdbTCP(AdbProtocolTCP):
    def shell(self, cmd: str, timeout: "int | float" = 20, shell_v2=True) -> ShellResult:
        """
        Args:
            cmd:
            timeout:
            shell_v2: True to use shell_v2 feature if available, which is faster and have separated stdout/stderr
        """
        shell_v2 = shell_v2 and self.features.shell_v2
        if shell_v2:
            with self.open_stream(f'shell,v2:{cmd}') as stream:
                data = stream.recv_until_close(timeout=timeout)
            return ShellResult.from_shell_v2(data)
        else:
            with self.open_stream(f'shell:{cmd}') as stream:
                data = stream.recv_until_close(timeout=timeout)
            return ShellResult.from_shell_v1(data)


if __name__ == '__main__':
    self = AdbTCP(port=16384)
    self.connect()
    s = self.shell('getprop')
    print(s)
    self.disconnect()
