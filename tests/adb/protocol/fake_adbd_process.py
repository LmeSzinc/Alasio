"""
A fake adbd in a process of its own, for the tests of the client.

The device of `fake_adbd` runs in the interpreter of the test, so the two ends
of the connection share a GIL, a traceback and a scheduler. A test that cares
about what a real peer sees — a FIN, a RST, the moment a read ends — runs the
device here instead: the connection then behaves the way it does between two
machines, limited only by the loopback.

The process opens the listener itself and prints its port, a port looked up by
the test could be taken between the lookup and the bind:

    READY <port>

Commands of the standard input, one per line:

    report    print the recordings of the device as one json line
    drop      close every connection, the listener stays open
    stop      print the recordings and exit

The standard input of a test that died reads end of file, which also exits the
process, so a failed test leaves no device behind.
"""

import json
import sys

from tests.adb.protocol.fake_adbd import FakeAdbd, ShellReply


def respond(command, shell_v2):
    """
    Answer a shell command with the command itself, the way echo would.

    Args:
        command (str): Command the host sent
        shell_v2 (bool): True when the stream uses the shell protocol v2

    Returns:
        ShellReply: The command as the output of the device
    """
    return ShellReply(stdout=command.encode() + b'\n')


def main():
    """Serve the host until the standard input asks the device to stop"""
    adbd = FakeAdbd(responder=respond)
    print(f'READY {adbd.port}', flush=True)
    try:
        while True:
            command = sys.stdin.readline()
            if not command:
                # the test is gone, the device must not outlive it
                break
            command = command.strip()
            if command == 'report':
                print(json.dumps(adbd.snapshot()), flush=True)
            elif command == 'drop':
                adbd.drop_connections()
            elif command == 'stop':
                break
    finally:
        try:
            print(json.dumps(adbd.snapshot()), flush=True)
        except OSError:
            # the test that reads the report is gone
            pass
        adbd.close()


if __name__ == '__main__':
    main()
