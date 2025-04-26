import os
import re
from dataclasses import dataclass

from alasio.ext.cache import cached_property, del_cached_property
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import atomic_read_text
from alasio.ext.path.calc import joinpath


def vbox_file_to_serial(file: str) -> str:
    """
    Args:
        file: Path to vbox file

    Returns:
        str: serial such as `127.0.0.1:5555`
    """
    # <Forwarding name="port2" proto="1" hostip="127.0.0.1" hostport="62026" guestport="5555"/>
    regex = re.compile('<*?hostport="(.*?)".*?guestport="5555"/>', re.M)
    file = atomic_read_text(file, encoding='utf-8', errors='ignore')
    if not file:
        return ''

    res = regex.search(file)
    if res:
        return f'127.0.0.1:{res.group(1)}'
    else:
        return ''


def get_serial_pair(serial):
    """
    Args:
        serial (str):

    Returns:
        str, str: `127.0.0.1:5555+{X}` and `emulator-5554+{X}`, 0 <= X <= 32
    """
    if serial.startswith('127.0.0.1:'):
        try:
            port = int(serial[10:])
            if 5555 <= port <= 5555 + 32:
                return f'127.0.0.1:{port}', f'emulator-{port - 1}'
        except (ValueError, IndexError):
            pass
    if serial.startswith('emulator-'):
        try:
            port = int(serial[9:])
            if 5554 <= port <= 5554 + 32:
                return f'127.0.0.1:{port + 1}', f'emulator-{port}'
        except (ValueError, IndexError):
            pass

    return None, None


def remove_duplicated_path(paths):
    """
    Args:
        paths (list[T]):

    Returns:
        list[T]:
    """
    paths = sorted(set(paths))
    dic = {}
    for path in paths:
        dic.setdefault(path.lower(), path)
    return list(dic.values())


def flatten_list(nested: "list[list[T]]") -> "list[T]":
    result = []
    for row in nested:
        result += row
    return result


class EmuType:
    # Values here must match those in argument.yaml EmulatorInfo.Emulator.option
    NoxPlayer = 'NoxPlayer'
    NoxPlayer64 = 'NoxPlayer64'
    NoxPlayerFamily = [NoxPlayer, NoxPlayer64]

    BlueStacks4 = 'BlueStacks4'
    BlueStacks5 = 'BlueStacks5'
    BlueStacks4HyperV = 'BlueStacks4HyperV'
    BlueStacks5HyperV = 'BlueStacks5HyperV'
    BlueStacksFamily = [BlueStacks4, BlueStacks5]

    LDPlayer3 = 'LDPlayer3'
    LDPlayer4 = 'LDPlayer4'
    LDPlayer9 = 'LDPlayer9'
    LDPlayerFamily = [LDPlayer3, LDPlayer4, LDPlayer9]

    MuMuPlayer = 'MuMuPlayer'
    MuMuPlayerX = 'MuMuPlayerX'
    MuMuPlayer12 = 'MuMuPlayer12'
    MuMuPlayerFamily = [MuMuPlayer, MuMuPlayerX, MuMuPlayer12]

    MEmuPlayer = 'MEmuPlayer'


class EmuStr(PathStr):
    def emutype(self) -> str:
        """
        Returns:
            str: Emulator type, such as Emulator.NoxPlayer
        """
        folder, _, exe = self.rpartition(os.sep)
        folder, _, dir1 = folder.rpartition(os.sep)
        folder, _, dir2 = folder.rpartition(os.sep)
        exe = exe.lower()
        dir1 = dir1.lower()
        dir2 = dir2.lower()
        if exe == 'nox.exe':
            if dir2 == 'nox':
                return EmuType.NoxPlayer
            elif dir2 == 'nox64':
                return EmuType.NoxPlayer64
            else:
                return EmuType.NoxPlayer
        if exe == 'bluestacks.exe':
            if dir1 in ['bluestacks', 'bluestacks_cn']:
                return EmuType.BlueStacks4
            elif dir1 in ['bluestacks_nxt', 'bluestacks_nxt_cn']:
                return EmuType.BlueStacks5
            else:
                return EmuType.BlueStacks4
        if exe == 'hd-player.exe':
            if dir1 in ['bluestacks', 'bluestacks_cn']:
                return EmuType.BlueStacks4
            elif dir1 in ['bluestacks_nxt', 'bluestacks_nxt_cn']:
                return EmuType.BlueStacks5
            else:
                return EmuType.BlueStacks5
        if exe == 'dnplayer.exe':
            if dir1 == 'ldplayer':
                return EmuType.LDPlayer3
            elif dir1 == 'ldplayer4':
                return EmuType.LDPlayer4
            elif dir1 == 'ldplayer9':
                return EmuType.LDPlayer9
            else:
                return EmuType.LDPlayer3
        if exe == 'nemuplayer.exe':
            if dir2 == 'nemu':
                return EmuType.MuMuPlayer
            elif dir2 == 'nemu9':
                return EmuType.MuMuPlayerX
            else:
                return EmuType.MuMuPlayer
        if exe == 'mumuplayer.exe':
            return EmuType.MuMuPlayer12
        if exe == 'memu.exe':
            return EmuType.MEmuPlayer

        return ''

    def iter_single_from_multi(self) -> str:
        """
        Convert a string that might be a multi-instance manager to its single instance executable.

        Yields:
            str: Path to emulator executable
        """
        folder, _, exe = self.rpartition(os.sep)
        if exe == 'HD-MultiInstanceManager.exe':
            yield joinpath(folder, 'HD-Player.exe')
            yield joinpath(folder, 'Bluestacks.exe')
        elif exe == 'MultiPlayerManager.exe':
            yield joinpath(folder, 'Nox.exe')
        elif exe == 'dnmultiplayer.exe':
            yield joinpath(folder, 'dnplayer.exe')
        elif exe == 'NemuMultiPlayer.exe':
            yield joinpath(folder, 'NemuPlayer.exe')
        elif exe == 'MuMuMultiPlayer.exe':
            yield joinpath(folder, 'MuMuPlayer.exe')
        elif exe == 'MuMuManager.exe':
            yield joinpath(folder, 'MuMuPlayer.exe')
        elif exe == 'MEmuConsole.exe':
            yield joinpath(folder, 'MEmu.exe')

    def single_to_console(self: str) -> str:
        """
        Convert a string that might be a single instance executable to its console.
        Emulators can only have one possible console, so it's return

        Returns:
            str: Path to emulator console
        """
        folder, _, exe = self.rpartition(os.sep)
        if exe == 'MuMuPlayer.exe':
            return joinpath(folder, 'MuMuManager.exe')
        elif exe == 'LDPlayer.exe':
            return joinpath(folder, 'ldconsole.exe')
        elif exe == 'dnplayer.exe':
            return joinpath(folder, 'ldconsole.exe')
        elif exe == 'Bluestacks.exe':
            return joinpath(folder, 'bsconsole.exe')
        elif exe == 'MEmu.exe':
            return joinpath(folder, 'memuc.exe')
        return ''


@dataclass
class EmulatorInstance:
    # Serial for adb connection
    serial: str
    # Emulator instance name, used for start/stop emulator
    name: str
    # Path to emulator .exe
    path: EmuStr

    def __str__(self):
        return f'{self.emutype}(serial="{self.serial}", name="{self.name}", path="{self.path}")'

    @cached_property
    def emutype(self) -> str:
        """
        Returns:
            str: Emulator type, such as Emulator.NoxPlayer
        """
        return self.path.emutype()

    def __eq__(self, other):
        if isinstance(other, str) and self.emutype == other:
            return True
        if isinstance(other, list) and self.emutype in other:
            return True
        if isinstance(other, EmulatorInstance):
            return super().__eq__(other) and self.emutype == other.emutype
        return super().__eq__(other)

    def __hash__(self):
        return hash(str(self))

    def __bool__(self):
        return True

    @cached_property
    def MuMuPlayer12_id(self):
        """
        Convert MuMu 12 instance name to instance id.
        Example names:
            MuMuPlayer-12.0-3
            YXArkNights-12.0-1

        Returns:
            int: Instance ID, or None if this is not a MuMu 12 instance
        """
        res = re.search(r'MuMuPlayer(?:Global)?-12.0-(\d+)', self.name)
        if res:
            return int(res.group(1))
        res = re.search(r'YXArkNights-12.0-(\d+)', self.name)
        if res:
            return int(res.group(1))

        return None

    @cached_property
    def LDPlayer_id(self):
        """
        Convert LDPlayer instance name to instance id.
        Example names:
            leidian0
            leidian1

        Returns:
            int: Instance ID, or None if this is not a LDPlayer instance
        """
        res = re.search(r'leidian(\d+)', self.name)
        if res:
            return int(res.group(1))

        return None


class EmulatorSearchBase:
    """
    Abstract base class, all classes to search emulators should implement these methods
    """

    @staticmethod
    def iter_running_emulator():
        """
        Yields:
            str: Path to emulator executables, may contains duplicate values
        """
        return

    @cached_property
    def all_emulators(self) -> "list[EmuStr]":
        """
        Get all emulators installed on current computer.
        """
        return []

    @cached_property
    def all_emulator_instances(self) -> "list[EmulatorInstance]":
        """
        Get all emulator instances installed on current computer.
        """
        return []

    @cached_property
    def all_emulator_serials(self) -> "list[str]":
        """
        Get all possible serials on current computer.
        """
        out = []
        for emulator in self.all_emulator_instances:
            out.append(emulator.serial)
            # Also add serial like `emulator-5554`
            port_serial, emu_serial = get_serial_pair(emulator.serial)
            if emu_serial:
                out.append(emu_serial)
        return out

    @cached_property
    def all_adb_binaries(self) -> "list[PathStr]":
        """
        Get all adb binaries of emulators on current computer.
        """
        return []

    def clear_emulator_cache(self):
        del_cached_property(self, 'all_emulators')
        del_cached_property(self, 'all_emulator_instances')
        del_cached_property(self, 'all_emulator_serials')
        del_cached_property(self, 'all_adb_binaries')
