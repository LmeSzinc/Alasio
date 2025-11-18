from alasio.adb.protocol.const import ShellResult
from alasio.adb.protocol.props import Props
from alasio.adb.protocol.tcp_protocol import AdbProtocolTCP
from alasio.ext.cache import cached_property
from alasio.logger import logger


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

    @cached_property
    def props(self) -> Props:
        """
        We cache `getprop` results instead of doing individual `getprop xxx`,
        because `getprop xxx` costs about 3ms and `getprop` costs about 7ms.
        If we need more than 2 keys (which is usually true), caching is faster.
        """
        data = self.shell('getprop', shell_v2=False)
        props = Props.from_getprop(data.stdout)
        return props

    @cached_property
    def sdk_ver(self) -> int:
        """
        Android SDK/API levels, see https://apilevels.com/
        """
        sdk = self.props.get_int('ro.build.version.sdk')
        if sdk:
            logger.info(f'[sdk_ver]: {sdk}')
        else:
            logger.error(f'SDK version invalid: {sdk}')
            sdk = 0
        return sdk


if __name__ == '__main__':
    self = AdbTCP(port=16384)
    self.connect()
    s = self.shell('getprop')
    print(s)
    self.disconnect()
