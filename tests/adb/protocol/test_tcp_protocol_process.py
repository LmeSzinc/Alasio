"""
Tests of the tcp transport against a fake adbd in another process.

`test_tcp_protocol.py` speaks to a fake device in the interpreter of the test:
both ends of the connection share a GIL, a traceback and a scheduler, and a
defect that hides behind that sharing can not show itself there. The device of
these tests runs in a process of its own, so the connection is the one two
machines have, limited only by the loopback.

Two things are only observable here:

- the end of a connection as the device sees it, a FIN (`eof`) and not a RST
  (`reset`) for every disconnect(), which is what a real adbd logs as a closed
  connection;
- the client ending on an event the kernel delivers to its process, not on
  something the interpreter of the device arranged for it.
"""

import json
import queue
import subprocess
import sys
import threading
import time

import pytest

from alasio.adb.protocol.const import AdbConnectionClosed, ShellResult
from alasio.adb.protocol.tcp_protocol import AdbProtocolTCP
from alasio.ext.env import ALASIO_ROOT

pytestmark = [
    pytest.mark.parallel,
]

# Seconds to wait for the device to start or to stop. A busy ci runner starts
# a python process slowly, a wedged one has to fail the test instead of
# hanging the suite
LIFE_TIMEOUT = 30
# Seconds to wait for an answer of the device
ANSWER_TIMEOUT = 30


class FakeAdbdProcess:
    """A fake adbd that serves in a process of its own"""

    def __init__(self):
        self._process = subprocess.Popen(
            [sys.executable, '-m', 'tests.adb.protocol.fake_adbd_process'],
            cwd=ALASIO_ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            # the traceback of a device that dies is the message of the
            # assertion that follows, a pipe nobody reads would lose it
            stderr=subprocess.STDOUT,
            text=True,
        )
        self._lines = queue.Queue()
        self._reader = threading.Thread(target=self._collect, daemon=True)
        self._reader.start()
        self.port = self._ready()

    def _collect(self):
        """Read the lines of the device into the queue, None at the end"""
        for line in self._process.stdout:
            self._lines.put(line.rstrip('\r\n'))
        self._lines.put(None)

    def _readline(self, timeout=ANSWER_TIMEOUT):
        """
        Read one line of the device.

        Args:
            timeout (float): Seconds to wait for the line. Defaults to ANSWER_TIMEOUT.

        Returns:
            str: The line, without its newline
        """
        try:
            line = self._lines.get(timeout=timeout)
        except queue.Empty:
            raise AssertionError(f'the fake adbd printed nothing for {timeout}s') from None
        assert line is not None, f'the fake adbd exited with code {self._process.poll()}'
        return line

    def _ready(self):
        """
        Wait for the listener of the device and read its port.

        Returns:
            int: Port the device listens on
        """
        line = self._readline(LIFE_TIMEOUT)
        ready, _, port = line.partition(' ')
        assert ready == 'READY', f'the fake adbd did not listen: {line!r}'
        return int(port)

    def _write(self, command):
        """
        Write one command to the standard input of the device.

        Args:
            command (str): Command to write
        """
        self._process.stdin.write(command + '\n')
        self._process.stdin.flush()

    def _command(self, command):
        """
        Run one command that answers with a json line.

        Args:
            command (str): Command to write

        Returns:
            dict: The answer of the device
        """
        self._write(command)
        return json.loads(self._readline())

    def report(self):
        """
        Read the recordings of the device so far.

        Returns:
            dict: What the device saw, see `FakeAdbd.snapshot()`
        """
        return self._command('report')

    def drop(self):
        """Close every connection of the device, the listener stays open"""
        self._write('drop')

    def close(self):
        """Stop the device and wait for its process"""
        if self._process.poll() is not None:
            return
        try:
            self._write('stop')
            # the report of the stop is the last line, the process exits after
            self._readline()
        except (OSError, AssertionError):
            pass
        try:
            self._process.stdin.close()
        except OSError:
            pass
        try:
            self._process.wait(timeout=LIFE_TIMEOUT)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=LIFE_TIMEOUT)


@pytest.fixture
def adbd():
    """
    A fake adbd in a process of its own.

    Yields:
        FakeAdbdProcess: The running device
    """
    device = FakeAdbdProcess()
    try:
        yield device
    finally:
        device.close()


def connect(adbd):
    """
    Connect a client to the device.

    Args:
        adbd (FakeAdbdProcess): Device to connect to

    Returns:
        AdbProtocolTCP: Connected client
    """
    client = AdbProtocolTCP(port=adbd.port)
    client.connect()
    return client


def ends_of(adbd, count, timeout=10):
    """
    Read the ends of the connections of the device.

    The device records the end of a connection in the thread that served it,
    just after the close that woke the client, so the record of the last
    connection may still be in flight when disconnect() returns.

    Args:
        adbd (FakeAdbdProcess): Device to read
        count (int): Number of ends expected
        timeout (float): Seconds to wait. Defaults to 10.

    Returns:
        list: How every connection ended, e.g. `['eof']`
    """
    deadline = time.perf_counter() + timeout
    while True:
        ends = adbd.report()['connection_ends']
        if len(ends) >= count:
            return ends
        assert time.perf_counter() < deadline, f'the device recorded {ends} of {count} ends'
        time.sleep(0.002)


def wait_for(predicate, timeout=10, message=None):
    """
    Wait until a predicate holds.

    Args:
        predicate (callable): None-ary callable, True when the state is the one waited for
        timeout (float): Seconds to wait. Defaults to 10.
        message (str): Message of the failure. Defaults to None.

    Raises:
        AssertionError: When the predicate never held
    """
    deadline = time.perf_counter() + timeout
    while not predicate():
        assert time.perf_counter() < deadline, message or 'the state was never reached'
        time.sleep(0.002)


class TestProcessPeer:
    """A device in another process, over a real tcp connection"""

    def test_a_command_returns_the_output(self, adbd):
        """A command crosses the socket and comes back as shell v2 output"""
        client = connect(adbd)
        try:
            with client.open_stream('shell,v2:echo hello') as stream:
                data = stream.recv_until_close(timeout=10)
        finally:
            client.disconnect()

        result = ShellResult.from_shell_v2(data)
        assert result.stdout == b'echo hello\n'
        assert result.exitcode == 0
        report = adbd.report()
        assert report['opens'] == ['shell,v2:echo hello']
        assert report['violations'] == []

    def test_disconnect_ends_the_connection_with_a_fin(self, adbd):
        """The device reads the end of the connection as an end of file

        A close that still holds unread data sends a RST instead, which a
        device does not read as a connection that ended.
        """
        client = connect(adbd)
        client.disconnect()

        report = adbd.report()
        assert report['connections'] == 1
        assert ends_of(adbd, 1) == ['eof']

    def test_disconnect_does_not_wait_for_the_timeout(self, adbd):
        """The dispatch thread ends on the FIN of the device, not on a timeout"""
        client = connect(adbd)
        thread = client._recv_thread
        sock = client._sock

        start = time.perf_counter()
        client.disconnect()
        elapsed = time.perf_counter() - start

        # the fallback of a device that does not answer waits the whole join
        # timeout, the FIN of this one arrives in the time of the loopback
        assert elapsed < 2
        assert thread is not None
        assert thread.is_alive() is False
        assert sock.fileno() == -1
        assert client._sock is None
        assert ends_of(adbd, 1) == ['eof']

    def test_a_connection_the_device_dropped_is_rebuilt(self, adbd):
        """A device that restarts does not leave the client dead forever"""
        client = connect(adbd)
        try:
            adbd.drop()
            wait_for(lambda: not client.is_connected, message='the client did not notice the dropped link')

            # a connection that is known to be dead is not used for a request
            # that would only time out
            with pytest.raises(AdbConnectionClosed):
                client.open_stream('shell:x')

            client.connect()
            with client.open_stream('shell,v2:echo again') as stream:
                data = stream.recv_until_close(timeout=10)
        finally:
            client.disconnect()

        assert ShellResult.from_shell_v2(data).stdout == b'echo again\n'
        report = adbd.report()
        assert report['connections'] == 2
        assert ends_of(adbd, 2)[-1] == 'eof'

    def test_many_rounds_leave_nothing_behind(self, adbd):
        """Every connection of a device that answers the close ends clean"""
        client = AdbProtocolTCP(port=adbd.port)
        threads = []
        for _ in range(10):
            client.connect()
            threads.append(client._recv_thread)
            client.disconnect()

        assert [thread.is_alive() for thread in threads] == [False] * 10
        report = adbd.report()
        assert report['connections'] == 10
        assert report['violations'] == []
        assert ends_of(adbd, 10) == ['eof'] * 10
