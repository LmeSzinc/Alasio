import socket
from struct import Struct
from threading import Lock, Thread
from time import time
from typing import Literal

from alasio.adb.protocol.cnxn import DeviceFeatures
from alasio.adb.protocol.const import *
from alasio.logger import logger


class AdbStreamTCP:
    def __init__(self, local_id: int, service: str, protocol: "AdbProtocolTCP"):
        self.local_id = local_id
        self.remote_id = 0  # will be set after A_OPEN
        self.service = service
        self.protocol = protocol

        self.state: "Literal['opening', 'opened', 'closing', 'closed']" = 'opening'
        # Internal event that will be set when message received
        self.send_event = Lock()
        self.send_event.acquire()  # service is send on stream opening
        self.recv_event = Lock()

        # result buffer
        self.data_buffer: "deque[bytes]" = deque()

    def __str__(self):
        return (f'{self.__class__.__name__}(remote_id={self.remote_id}, local_id={self.local_id}, '
                f'state={self.state}, service={self.service})')

    __repr__ = __str__

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self):
        """
        Raises:
            AdbConnectionTimeout:
        """
        self.protocol.close_stream(self)

    def recv_until_close(self, timeout: "int | float" = 20) -> bytes:
        while 1:
            # timeout on 1ms because time.time() has accuracy of 0.5ms
            if timeout < 0.001:
                raise AdbStreamTimeout('Timeout when receiving stream until close')
            start = time()
            if self.recv_event.acquire(timeout=timeout):
                if self.state == 'closed' or self.state == 'closing':
                    break
                # else: message
            timeout -= time() - start

        # build output
        data = b''.join(self.data_buffer)
        self.data_buffer.clear()
        return data


class AdbProtocolTCP:
    def __init__(self, host='127.0.0.1', port=5555):
        """
        Args:
            host (str):
            port (int):
        """
        super().__init__()
        self.host = host
        self.port = port
        self.timeout = 5

        self._connect_lock = Lock()
        self._send_lock = Lock()
        self._sock: "socket.socket | None" = None
        self._recv_thread: "Thread | None" = None

        # internal states
        self._message_struct = Struct('<6I')
        # maximum payload that device claims, init with our MAX_PAYLOAD
        self._max_payload = ADB_MAX_PAYLOAD

        # local stream ID pool, init with 8 IDs, starting from 1
        self._stream_id_pool = set(range(1, 9))
        self._stream_id_next = 9
        self._stream_dict: "dict[int, AdbStreamTCP]" = {}

        # device features
        self.features = DeviceFeatures([])

    def _stream_id_allocate(self) -> int:
        # reuse existing ID first
        pool = self._stream_id_pool
        try:
            return pool.pop()
        except KeyError:
            pass
        # pool exhausted, add 8 news (7 added to pool, 1 return)
        new = self._stream_id_next
        for n in range(new + 1, self._stream_id_next + 8):
            pool.add(n)
        return new

    def _stream_id_release(self, n: int):
        self._stream_id_pool.add(n)

    def connect(self):
        with self._connect_lock:
            # check if already connected
            if self._sock is not None:
                return

            addr = f'{self.host}:{self.port}'
            logger.info(f'Connecting to {addr}')

            # open TCP connection
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # may raise ConnectionRefusedError
            sock.connect((self.host, self.port))
            sock.settimeout(self.timeout)

            cnxn = (
                b"host::features=shell_v2,cmd,stat_v2,ls_v2,fixed_push_mkdir,apex,abb,"
                b"fixed_push_symlink_timestamp,abb_exec,remount_shell,track_app,"
                b"sendrecv_v2,sendrecv_v2_brotli,sendrecv_v2_lz4,sendrecv_v2_zstd,sendrecv_v2_dry_run_send"
            )
            try:
                # send CNXN
                self.message_send(sock, CNXN, ADB_VERSION, ADB_MAX_PAYLOAD, cnxn)

                # recv CNXN from device
                command, arg0, arg1, data = self.message_recv(sock)
                if command == CNXN:
                    logger.info(f'Connected to {addr}')
                    self._sock = sock
                    self._max_payload = min(arg1, ADB_MAX_PAYLOAD)
                    self.features = DeviceFeatures.from_cnxn(data)
                else:
                    raise AdbMessageInvalid(f'Expect {CNXN} after connection but got {command}')
            except Exception:
                # cleanup on error
                try:
                    sock.close()
                except Exception:
                    pass
                self.features = DeviceFeatures([])
                raise

            # start recv thread
            self._recv_thread = Thread(target=self._task_dispatch_message, daemon=True)
            self._recv_thread.start()

    def disconnect(self):
        with self._connect_lock:
            # check if already disconnected
            if self._sock is None:
                return

            addr = f'{self.host}:{self.port}'
            logger.info(f'Disconnecting to {addr}')

            # close TCP connection
            sock = self._sock
            self._sock = None
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass

            # close stream
            # we don't need graceful stream close before socket close, because adbd will do anyway
            # so we just release resources on our side
            stream_dict = self._stream_dict
            self._stream_dict = {}
            for local_id in stream_dict:
                self._stream_id_release(local_id)

            # stop recv thread
            thread = self._recv_thread
            self._recv_thread = None
            if thread and thread.is_alive():
                thread.join(timeout=2)

            logger.info(f'Disconnected to {addr}')

    def message_send(self, sock: socket.socket, command: bytes, arg0, arg1, data=b''):
        """
        Raises:
            AdbConnectionClosed:
            AdbConnectionTimeout:
        """
        data_length = len(data)
        data_check = sum(data) & 0xFFFFFFFF
        command = int.from_bytes(command, 'little')
        magic = command ^ 0xFFFFFFFF

        header = self._message_struct.pack(command, arg0, arg1, data_length, data_check, magic)
        payload = header + data

        try:
            with self._send_lock:
                sock.sendall(payload)
        except (ConnectionError, OSError) as e:
            # BrokenPipeError
            # ConnectionAbortedError
            # ConnectionResetError
            raise AdbConnectionClosed(f'{e.__class__.__name__} while sending: {e}')
        except socket.timeout as e:
            raise AdbConnectionTimeout(f'{e.__class__.__name__} while sending: {e}')

    def recv_exact(self, sock: socket.socket, length: int, header_timeout=True) -> bytes:
        """
        Raises:
            AdbConnectionClosed:
            AdbConnectionTimeout:
        """
        # fast path for receiving message header
        if header_timeout:
            try:
                data = sock.recv(length)
            except (ConnectionError, OSError) as e:
                raise AdbConnectionClosed(f'{e.__class__.__name__} when receiving header: {e}')
            except socket.timeout as e:
                raise AdbConnectionTimeout(f'{e.__class__.__name__} when receiving header: {e}')
        else:
            # wait until getting any data
            # No `sock.settimeout(None)` because setting timeout between None and 5 back and forth
            # might cause race condition, just wait
            while 1:
                try:
                    data = sock.recv(length)
                    break
                except (ConnectionError, OSError) as e:
                    raise AdbConnectionClosed(f'{e.__class__.__name__}: {e}')
                except socket.timeout:
                    continue

        total = len(data)
        if not total:
            raise AdbConnectionClosed('No data when receiving header')
        if total >= length:
            return data

        # receive more
        data = deque([data])
        while 1:
            try:
                chunk = sock.recv(length)
            except (ConnectionError, OSError) as e:
                raise AdbConnectionClosed(f'{e.__class__.__name__} when receiving chunk: {e}')
            except socket.timeout as e:
                raise AdbConnectionTimeout(f'{e.__class__.__name__} when receiving chunk: {e}')
            if not chunk:
                raise AdbConnectionClosed('No data when receiving chunk')

            total += len(chunk)
            data.append(chunk)
            if total >= length:
                break

        return b''.join(data)

    def message_recv(self, stream: socket.socket, header_timeout=True) -> "tuple[bytes, int, int, bytes]":
        """
        Receive one message from stream

        Raises:
            AdbConnectionClosed:
            AdbConnectionTimeout:
            AdbMessageValidateError:
        """
        header = self.recv_exact(stream, 24, header_timeout=header_timeout)
        command, arg0, arg1, data_length, data_check, magic = self._message_struct.unpack(header)

        # check command
        command_name = header[:4]
        if command_name not in ADB_COMMANDS:
            raise AdbMessageInvalid(f'Invalid command {command_name}, header={header}')

        # validate magic
        expected_magic = command ^ 0xFFFFFFFF
        if magic != expected_magic:
            raise AdbMessageInvalid(f'Magic not match, expect {expected_magic}, got {magic}')

        # recv payload
        if data_length > 0:
            payload = self.recv_exact(stream, data_length)
            # validate checksum
            checksum = sum(payload) & 0xFFFFFFFF
            if checksum != data_check:
                raise AdbMessageInvalid(f'Checksum not match, expect {data_check}, got {checksum}')
        else:
            payload = b''

        return command_name, arg0, arg1, payload

    def open_stream(self, service: str) -> AdbStreamTCP:
        """
        Args:
            service: Command like "shell:echo hello"
        """
        with self._connect_lock:
            local_id = self._stream_id_allocate()
            stream = AdbStreamTCP(local_id, service, protocol=self)
            self._stream_dict[local_id] = stream

        try:
            # send OPEN
            service_data = service.encode('utf-8')
            if not service_data.endswith(b'\x00'):
                service_data += b'\x00'
            self.message_send(self._sock, OPEN, stream.local_id, 0, service_data)

            # wait until opened
            # use connection-level timeout as this should be fast and not related to stream
            timeout = self.timeout
            while 1:
                # timeout on 1ms because time.time() has accuracy of 0.5ms
                if timeout < 0.001:
                    raise AdbConnectionTimeout(f'Timeout when opening stream {stream}')
                start = time()
                if stream.send_event.acquire(timeout=timeout):
                    print(stream)
                    if stream.state == 'opened':
                        break
                    if stream.state == 'closed':
                        break
                    # else: unknown message (not OPEN)
                timeout -= time() - start
        except Exception:
            # drop stream on error
            with self._connect_lock:
                self._stream_dict.pop(local_id, None)
                self._stream_id_release(local_id)
            raise

        # opened
        return stream

    def close_stream(self, stream: AdbStreamTCP):
        """
        Close a stream
        """
        state = stream.state
        if state == 'closed':
            return

        remote_id = stream.remote_id
        if not remote_id:
            logger.warning(f'Cannot close stream without remote_id: {stream}')
            return

        # request to close
        if state == 'opened' or state == 'opening':
            self.message_send(self._sock, CLSE, stream.local_id, remote_id)
        # wait until closed
        # use connection-level timeout as this should be fast and not related to stream
        if not stream.recv_event.acquire(timeout=self.timeout):
            raise AdbConnectionTimeout(f'Timeout when closing stream {stream}')

    def sendto_stream(self, stream: AdbStreamTCP, data: bytes):
        remote_id = stream.remote_id
        if not remote_id:
            logger.warning(f'Cannot sendto stream without remote_id: {stream}')
            return

        self.message_send(self._sock, WRTE, stream.local_id, remote_id, data)
        # just info that any message is sent, no need to wait
        stream.send_event.acquire(blocking=False)

        # wait OKAY
        # use connection-level timeout as this should be fast and not related to stream
        if not stream.send_event.acquire(timeout=self.timeout):
            raise AdbConnectionTimeout(f'Timeout when sendto stream {stream}')

    def _dispatch_message(self, command: bytes, remote_id: int, local_id: int, data: bytes):
        try:
            stream = self._stream_dict[local_id]
        except KeyError:
            # message from unknown stream id, drop
            return

        # stream opened
        if command == OKAY:
            if stream.state == 'opening':
                # received first OKAY
                stream.remote_id = remote_id
                stream.state = 'opened'
                try:
                    stream.send_event.release()
                except RuntimeError:
                    # allow double release, we just need to notify main thread message received
                    pass
            elif stream.state == 'opened':
                # received OKAY from sent message
                try:
                    stream.send_event.release()
                except RuntimeError:
                    pass
            else:
                logger.warning(f'Unexpected command={command} on stream {stream}')

        # dispatch message to stream
        elif command == WRTE:
            if stream.state == 'opened':
                # response device with OKAY
                self.message_send(self._sock, OKAY, stream.local_id, stream.remote_id)
                stream.data_buffer.append(data)
                try:
                    stream.recv_event.release()
                except RuntimeError:
                    pass
            else:
                logger.warning(f'Unexpected command={command} on stream {stream}')

        # stream close
        elif command == CLSE:
            # release stream
            with self._connect_lock:
                self._stream_dict.pop(local_id, None)
                self._stream_id_release(local_id)
            # confirm closed
            stream.state = 'closed'
            try:
                stream.send_event.release()
            except RuntimeError:
                pass
            try:
                stream.recv_event.release()
            except RuntimeError:
                pass

    def _task_dispatch_message(self):
        while 1:
            sock = self._sock
            if sock is None:
                break
            try:
                msg = self.message_recv(sock, header_timeout=False)
            except (AdbConnectionClosed, AdbConnectionTimeout, AdbMessageInvalid):
                break
            print(msg)
            self._dispatch_message(*msg)
