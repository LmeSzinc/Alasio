import codecs
import os
import re
import winreg
from dataclasses import dataclass

from alasio.ext.cache import cached_property
from alasio.ext.path import PathStr
from alasio.ext.path.calc import normpath
from alasio.ext.path.iter import CachePathExists, iter_files
from alasio.ext.pool import WORKER_POOL
from alasio.ext.proc import process_iter
from alasio.device.search.base import EmuStr, EmuType, EmulatorInstance, EmulatorSearchBase, flatten_list, \
    remove_duplicated_path, vbox_file_to_serial


@dataclass
class RegValue:
    name: str
    value: str
    typ: int


def list_reg(reg) -> "list[RegValue]":
    """
    List all reg values in a reg key
    """
    rows = []
    index = 0
    try:
        while 1:
            value = RegValue(*winreg.EnumValue(reg, index))
            index += 1
            rows.append(value)
    except OSError:
        pass
    return rows


def list_key(reg) -> "list[str]":
    """
    List all keys in a reg key
    """
    rows = []
    index = 0
    try:
        while 1:
            value = winreg.EnumKey(reg, index)
            index += 1
            rows.append(value)
    except OSError:
        pass
    return rows


def iter_user_assist():
    """
    Get recently executed programs in UserAssist
    https://github.com/forensicmatt/MonitorUserAssist

    Yields:
        EmuStr: Path to emulator executables, may contains duplicate values
    """
    path = r'Software\Microsoft\Windows\CurrentVersion\Explorer\UserAssist'
    # {XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}\xxx.exe
    regex_hash = re.compile(r'{.*}')
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as reg:
            folders = list_key(reg)
    except FileNotFoundError:
        return

    for folder in folders:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f'{path}\\{folder}\\Count') as reg:
                for key in list_reg(reg):
                    key = codecs.decode(key.name, 'rot-13')
                    # Skip those with hash
                    if regex_hash.search(key):
                        continue
                    file = EmuStr.new(key)
                    yield file
                    for single in file.iter_single_from_multi():
                        yield EmuStr(single)
        except FileNotFoundError:
            # FileNotFoundError: [WinError 2] 系统找不到指定的文件。
            # Might be a random directory without "Count" subdirectory
            continue


def iter_mui_cache():
    """
    Iter emulator executables that has ever run.
    http://what-when-how.com/windows-forensic-analysis/registry-analysis-windows-forensic-analysis-part-8/
    https://3gstudent.github.io/%E6%B8%97%E9%80%8F%E6%8A%80%E5%B7%A7-Windows%E7%B3%BB%E7%BB%9F%E6%96%87%E4%BB%B6%E6
    %89%A7%E8%A1%8C%E8%AE%B0%E5%BD%95%E7%9A%84%E8%8E%B7%E5%8F%96%E4%B8%8E%E6%B8%85%E9%99%A4

    Yields:
        EmuStr: Path to emulator executable, may contains duplicate values
    """
    path = r'Software\Classes\Local Settings\Software\Microsoft\Windows\Shell\MuiCache'
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as reg:
            rows = list_reg(reg)
    except FileNotFoundError:
        return

    # Remove app names
    # E:\ProgramFiles\MuMu\emulator\nemu\EmulatorShell\NemuPlayer.exe.FriendlyAppName
    regex = re.compile(r'(^.*\.exe)\.')
    for row in rows:
        res = regex.search(row.name)
        if not res:
            continue
        file = EmuStr.new(res.group(1))
        yield file
        for single in file.iter_single_from_multi():
            yield EmuStr(single)


def get_install_dir_from_reg(path, key):
    """
    Args:
        path (str): f'SOFTWARE\\leidian\\ldplayer'
        key (str): 'InstallDir'

    Returns:
        str: Installation dir or ''
    """
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as reg:
            root = winreg.QueryValueEx(reg, key)[0]
            return root
    except FileNotFoundError:
        pass
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as reg:
            root = winreg.QueryValueEx(reg, key)[0]
            return root
    except FileNotFoundError:
        pass

    return ''


def iter_known_registry():
    """
    Iter emulator executables using hardcoded registry path.

    Yields:
        EmuStr: Path to emulator executable, may contains duplicate values
    """
    for path in [
        r'SOFTWARE\leidian\ldplayer',
        r'SOFTWARE\leidian\ldplayer9'
    ]:
        file = get_install_dir_from_reg(path, 'InstallDir')
        if file:
            file = EmuStr.new(file)
            file = f'{file}{os.sep}dnplayer.exe'
            file = EmuStr(file)
            yield file


def iter_uninstall_registry():
    """
    Iter emulator uninstaller from registry.

    Yields:
        EmuStr: Path to uninstall exe file
    """
    known_uninstall_registry_path = [
        r'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall',
        r'Software\Microsoft\Windows\CurrentVersion\Uninstall'
    ]
    known_emulator_registry_name = [
        'Nox',
        'Nox64',
        'BlueStacks',
        'BlueStacks_nxt',
        'BlueStacks_cn',
        'BlueStacks_nxt_cn',
        'LDPlayer',
        'LDPlayer4',
        'LDPlayer9',
        'leidian',
        'leidian4',
        'leidian9',
        'Nemu',
        'Nemu9',
        'MuMuPlayer-12.0'
        'MEmu',
    ]
    for path in known_uninstall_registry_path:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as reg:
                software_list = list_key(reg)
        except FileNotFoundError:
            continue
        for software in software_list:
            if software not in known_emulator_registry_name:
                continue
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f'{path}\\{software}') as software_reg:
                    uninstall = winreg.QueryValueEx(software_reg, 'UninstallString')[0]
            except FileNotFoundError:
                continue
            if not uninstall:
                continue
            # UninstallString is like:
            # C:\Program Files\BlueStacks_nxt\BlueStacksUninstaller.exe -tmp
            # "E:\ProgramFiles\Microvirt\MEmu\uninstall\uninstall.exe" -u
            # Extract path in ""
            res = re.search('"(.*?)"', uninstall)
            uninstall = res.group(1) if res else uninstall

            # Try to remove cmd args
            uninstall = normpath(uninstall)
            folder, sep, file_args = uninstall.rpartition(os.sep)
            file, space, _ = file_args.partition(' ')
            exe = EmuStr(f'{folder}{sep}{file}')
            yield exe


def iter_instances(emu: EmuStr):
    """
    Yields:
        EmulatorInstance: Emulator instances found in this emulator
    """
    emutype = emu.emutype()
    if emutype in EmuType.NoxPlayerFamily:
        # ./BignoxVMS/{name}/{name}.vbox

        for folder in emu.with_name('BignoxVMS').iter_folders():
            for file in folder.iter_files('.vbox'):
                serial = vbox_file_to_serial(file)
                if serial:
                    yield EmulatorInstance(
                        serial=serial,
                        name=os.path.basename(folder),
                        path=emu,
                    )
    elif emutype == EmuType.BlueStacks5:
        # Get UserDefinedDir, where BlueStacks stores data
        folder = None
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\BlueStacks_nxt") as reg:
                folder = winreg.QueryValueEx(reg, 'UserDefinedDir')[0]
        except FileNotFoundError:
            pass
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\BlueStacks_nxt_cn") as reg:
                folder = winreg.QueryValueEx(reg, 'UserDefinedDir')[0]
        except FileNotFoundError:
            pass
        if not folder:
            return
        # Read {UserDefinedDir}/bluestacks.conf
        file = PathStr.new(folder).joinpath('bluestacks.conf')
        try:
            content = file.atomic_read_text()
        except FileNotFoundError:
            return
        if not content:
            return
        # bst.instance.Nougat64.adb_port="5555"
        emulators = re.findall(r'bst.instance.(\w+).status.adb_port="(\d+)"', content)
        for info in emulators:
            yield EmulatorInstance(
                serial=f'127.0.0.1:{info[1]}',
                name=info[0],
                path=emu,
            )
    elif emutype == EmuType.BlueStacks4:
        # ../Engine/Android
        regex = re.compile(r'^Android')
        for name in emu.uppath().with_name('Engine').iter_foldernames():
            res = regex.match(name)
            if not res:
                continue
            # Serial from BlueStacks4 are not static, they get increased on every emulator launch
            # Assume all use 127.0.0.1:5555
            yield EmulatorInstance(
                serial=f'127.0.0.1:5555',
                name=name,
                path=emu
            )
    elif emutype in EmuType.LDPlayerFamily:
        # ./vms/leidian0
        regex = re.compile(r'^leidian(\d+)$')
        for name in emu.with_name('vms').iter_foldernames():
            res = regex.match(name)
            if not res:
                continue
            # LDPlayer has no forward port config in .vbox file
            # Ports are auto increase, 5555, 5557, 5559, etc
            port = int(res.group(1)) * 2 + 5555
            yield EmulatorInstance(
                serial=f'127.0.0.1:{port}',
                name=name,
                path=emu
            )
    elif emutype == EmuType.MuMuPlayer:
        # MuMu has no multi instances, on 7555 only
        yield EmulatorInstance(
            serial='127.0.0.1:7555',
            name='',
            path=emu,
        )
    elif emutype == EmuType.MuMuPlayerX:
        # vms/nemu-12.0-x64-default
        for folder in emu.uppath().with_name('vms').iter_folders():
            for file in folder.iter_files('.nemu'):
                serial = vbox_file_to_serial(file)
                if serial:
                    yield EmulatorInstance(
                        serial=serial,
                        name=folder.name,
                        path=emu,
                    )
    elif emutype == EmuType.MuMuPlayer12:
        # vms/MuMuPlayer-12.0-0
        for folder in emu.uppath().with_name('vms').iter_folders():
            for file in folder.iter_files('.nemu'):
                serial = vbox_file_to_serial(file)
                if serial:
                    yield EmulatorInstance(
                        serial=serial,
                        name=folder.name,
                        path=emu,
                    )
                # Fix for MuMu12 v4.0.4, default instance of which has no forward record in vbox config
                else:
                    instance = EmulatorInstance(
                        serial=serial,
                        name=folder.name,
                        path=emu,
                    )
                    if instance.MuMuPlayer12_id:
                        instance.serial = f'127.0.0.1:{16384 + 32 * instance.MuMuPlayer12_id}'
                        yield instance
    elif emutype == EmuType.MEmuPlayer:
        # ./MemuHyperv VMs/{name}/{name}.memu
        for folder in emu.with_name('MemuHyperv VMs').iter_folders():
            for file in folder.iter_files('.memu'):
                serial = vbox_file_to_serial(file)
                if serial:
                    yield EmulatorInstance(
                        serial=serial,
                        name=os.path.basename(folder),
                        path=emu,
                    )


def list_instances(emu: EmuStr) -> "list[EmulatorInstance]":
    """
    Get all instances from this emulator
    """
    return list(iter_instances(emu))


def iter_adb_binaries(emu: EmuStr):
    """
    Yields:
        PathStr: Filepath to adb binaries found in this emulator
    """
    if emu == EmuType.NoxPlayerFamily:
        exe = emu.with_name('nox_adb.exe')
        if exe.exists():
            yield exe
    if emu == EmuType.MuMuPlayerFamily:
        # From MuMu9\emulator\nemu9\EmulatorShell\nemuplayer.exe
        # to MuMu9\emulator\nemu9\vmonitor\bin\adb_server.exe
        exe = emu.uppath(2).joinpath('vmonitor/bin/adb_server.exe')
        if exe.exists():
            yield exe

    # All emulators have adb.exe
    exe = emu.with_name('adb.exe')
    if exe.exists():
        yield exe


def list_adb_binaries(emu: EmuStr) -> "list[PathStr]":
    """
    Get all adb binaries found in this emulator
    """
    return list(iter_adb_binaries(emu))


class EmulatorSearchWindows(EmulatorSearchBase, CachePathExists):
    def search_user_assist(self):
        output = set()
        for file in iter_user_assist():
            if file.emutype() and self.path_exists(file):
                output.add(file)
        return output

    def search_mui_cache(self):
        output = set()
        for file in iter_mui_cache():
            if file.emutype() and self.path_exists(file):
                output.add(file)
        return output

    def search_known_registry(self):
        output = set()
        for file in iter_known_registry():
            if file.emutype() and self.path_exists(file):
                output.add(file)
        return output

    @staticmethod
    def search_uninstall_registry():
        output = set()
        for uninstall in iter_uninstall_registry():
            # Find emulator executable from uninstaller
            for file in iter_files(uninstall.uppath(), ext='.exe'):
                file = EmuStr(file)
                if file.emutype():
                    output.add(file)
            # Find from parent directory
            for file in iter_files(uninstall.uppath(2), ext='.exe'):
                file = EmuStr(file)
                if file.emutype():
                    output.add(file)
            # MuMu specific directory
            for file in iter_files(uninstall.with_name('EmulatorShell'), ext='.exe'):
                file = EmuStr(file)
                if file.emutype():
                    output.add(file)
            for file in iter_files(uninstall.with_name('shell'), ext='.exe'):
                file = EmuStr(file)
                if file.emutype():
                    output.add(file)

        return output

    def search_running_emulator(self) -> "set[PathStr]":
        output = set()
        for pid, cmdline in process_iter():
            file = EmuStr(cmdline[0])
            if file.emutype() and self.path_exists(file):
                output.add(file)
        return output

    @cached_property
    def all_emulators(self) -> "list[EmuStr]":
        """
        Get all emulators installed on current computer.
        """
        # output = set()
        # output |= self.search_running_emulator()
        # output |= self.search_uninstall_registry()
        # output |= self.search_known_registry()
        # output |= self.search_mui_cache()
        # output |= self.search_user_assist()

        # Search with all methods
        func = [
            # search_running_emulator first, this is the slowest
            self.search_running_emulator,
            self.search_uninstall_registry,
            self.search_mui_cache,
            self.search_user_assist,
            self.search_known_registry,
        ]
        results = WORKER_POOL.thread_funcmap(func)
        output = flatten_list(results)

        return remove_duplicated_path(output)

    @cached_property
    def all_emulator_instances(self) -> "list[EmulatorInstance]":
        """
        Get all emulator instances installed on current computer.
        """
        # output = []
        # for emulator in self.all_emulators:
        #     output += list(iter_instances(emulator))

        results = WORKER_POOL.thread_map(list_instances, self.all_emulators)
        output = flatten_list(results)

        output = sorted(set(output), key=lambda x: str(x))
        return output

    @cached_property
    def all_adb_binaries(self) -> "list[EmulatorInstance]":
        """
        Get all emulator instances installed on current computer.
        """
        # output = []
        # for emulator in self.all_emulators:
        #     output += list(list_adb_binaries(emulator))

        results = WORKER_POOL.thread_map(list_adb_binaries, self.all_emulators)
        output = flatten_list(results)

        output = sorted(set(output), key=lambda x: str(x))
        return output


if __name__ == '__main__':
    import time

    count = 1000000
    start = time.perf_counter()
    o = EmulatorSearchWindows().all_adb_binaries
    print(time.perf_counter() - start)

    start = time.perf_counter()
    o = EmulatorSearchWindows().all_adb_binaries
    print(time.perf_counter() - start)

    for b in o:
        print(b)