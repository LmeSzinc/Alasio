import os

from alasio.device.search.base import *
from alasio.device.search.windows_reg import *
from alasio.ext.cache import cached_property
from alasio.ext.concurrent.threadpool import THREAD_POOL
from alasio.ext.path import PathStr
from alasio.ext.path.iter import CachePathExists
from alasio.ext.proc import process_iter


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
        except OSError:
            pass
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\BlueStacks_nxt_cn") as reg:
                folder = winreg.QueryValueEx(reg, 'UserDefinedDir')[0]
        except OSError:
            pass
        if not folder:
            return
        # Read {UserDefinedDir}/bluestacks.conf
        file = PathStr.new(folder).joinpath('bluestacks.conf')
        try:
            content = file.atomic_read_text()
        except OSError:
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
            has_emulator_shell = False
            has_shell = False
            # Find emulator executable from uninstaller
            for file in uninstall.uppath().iter_files(ext='.exe'):
                name = file.name
                if name == 'EmulatorShell':
                    has_emulator_shell = True
                if name == 'shell':
                    has_shell = True
                if file.emutype():
                    output.add(file)
            # Find from parent directory
            for file in uninstall.uppath(2).iter_files(ext='.exe'):
                if file.emutype():
                    output.add(file)
            # MuMu specific directory
            if has_emulator_shell:
                for file in uninstall.with_name('EmulatorShell').iter_files(ext='.exe'):
                    if file.emutype():
                        output.add(file)
            if has_shell:
                for file in uninstall.with_name('shell').iter_files(ext='.exe'):
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
        results = THREAD_POOL.thread_funcmap(func)
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

        results = THREAD_POOL.thread_map(list_instances, self.all_emulators)
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

        results = THREAD_POOL.thread_map(list_adb_binaries, self.all_emulators)
        output = flatten_list(results)

        output = sorted(set(output), key=lambda x: str(x))
        return output


if __name__ == '__main__':
    import time

    count = 1000000
    start = time.perf_counter()
    o = EmulatorSearchWindows().all_adb_binaries
    print(time.perf_counter() - start)

    # start = time.perf_counter()
    # o = EmulatorSearchWindows().all_adb_binaries
    # print(time.perf_counter() - start)

    for b in o:
        print(b)
