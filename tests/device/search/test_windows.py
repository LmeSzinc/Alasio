import os

import pytest

from alasio.device.search.base import EmulatorInstance, EmuStr, EmuType
from alasio.ext.env import WINDOWS
from alasio.testing.filesystem import fs  # noqa: F401
from tests.device.search._registry_stub import FakeWinreg

if WINDOWS:
    from alasio.device.search import windows
    from alasio.device.search.windows import EmulatorSearchWindows, list_adb_binaries, list_instances

pytestmark = [
    pytest.mark.parallel,
    pytest.mark.skipif(not WINDOWS, reason='Windows only'),
]

VBOX_FORWARDING = (
    '<Forwarding name="port2" proto="1" hostip="127.0.0.1" '
    'hostport="{host}" guestport="5555"/>'
)

# One executable path per Nox family member, the family constant is the source
# of truth for the members themselves
NOX_PATHS = {
    EmuType.NoxPlayer: 'E:/Nox/bin/Nox.exe',
    EmuType.NoxPlayer64: 'E:/Nox64/bin/Nox.exe',
}


def _nox_emu():
    return EmuStr.new(NOX_PATHS[EmuType.NoxPlayer])


def _bluestacks4_emu():
    return EmuStr.new('C:/Program Files/BlueStacks/Bluestacks.exe')


def _bluestacks5_emu():
    return EmuStr.new('E:/BlueStacks_nxt/HD-Player.exe')


def _ldplayer_emu(version=9):
    return EmuStr.new(f'E:/LDPlayer{version}/dnplayer.exe')


def _mumu_emu():
    return EmuStr.new('E:/MuMu/emulator/nemu/EmulatorShell/NemuPlayer.exe')


def _mumu_x_emu():
    return EmuStr.new('E:/MuMu9/emulator/nemu9/EmulatorShell/NemuPlayer.exe')


def _mumu_12_emu():
    return EmuStr.new('E:/MuMuPlayer-12.0/shell/MuMuPlayer.exe')


def _memu_emu():
    return EmuStr.new('E:/Microvirt/MEmu/MEmu.exe')


class TestIterInstances:
    def test_unknown_executable_yields_nothing(self):
        assert list(windows.iter_instances(EmuStr.new('E:/Foo/Bar.exe'))) == []

    def test_nox_instances(self, fs):
        fs.create_dir('E:/Nox/bin/BignoxVMS/Nox64_7')
        fs.create_file(
            'E:/Nox/bin/BignoxVMS/Nox64_7/Nox64_7.vbox',
            contents=VBOX_FORWARDING.format(host=62026),
        )
        fs.create_dir('E:/Nox/bin/BignoxVMS/not_an_instance')
        assert [str(x) for x in windows.iter_instances(_nox_emu())] == [
            'NoxPlayer(serial="127.0.0.1:62026", name="Nox64_7", path="E:/Nox/bin/Nox.exe")'
        ]

    def test_nox_without_the_config_folder(self):
        assert list(windows.iter_instances(_nox_emu())) == []

    def test_bluestacks4_instances(self, fs):
        """`../Engine/Android*` next to the emulator folder, port is always 5555."""
        fs.create_dir('C:/Program Files/Engine/Android')
        fs.create_dir('C:/Program Files/Engine/Android2')
        fs.create_dir('C:/Program Files/Engine/Other')
        assert [str(x) for x in windows.iter_instances(_bluestacks4_emu())] == [
            'BlueStacks4(serial="127.0.0.1:5555", name="Android", '
            'path="C:/Program Files/BlueStacks/Bluestacks.exe")',
            'BlueStacks4(serial="127.0.0.1:5555", name="Android2", '
            'path="C:/Program Files/BlueStacks/Bluestacks.exe")',
        ]

    def test_bluestacks5_instances(self, fs, monkeypatch):
        """The instance list comes from `bluestacks.conf` in UserDefinedDir."""
        monkeypatch.setattr(
            windows, 'winreg', FakeWinreg({r'SOFTWARE\BlueStacks_nxt': {'values': {'UserDefinedDir': 'E:/BS5'}}})
        )
        fs.create_file(
            'E:/BS5/bluestacks.conf',
            contents=(
                'bst.instance.Nougat64.status.adb_port="5555"\n'
                'bst.instance.Pie64.status.adb_port="5565"\n'
            ),
        )
        assert [str(x) for x in windows.iter_instances(_bluestacks5_emu())] == [
            'BlueStacks5(serial="127.0.0.1:5555", name="Nougat64", path="E:/BlueStacks_nxt/HD-Player.exe")',
            'BlueStacks5(serial="127.0.0.1:5565", name="Pie64", path="E:/BlueStacks_nxt/HD-Player.exe")',
        ]

    def test_bluestacks5_with_the_chinese_registry_key(self, fs, monkeypatch):
        monkeypatch.setattr(
            windows,
            'winreg',
            FakeWinreg({r'SOFTWARE\BlueStacks_nxt_cn': {'values': {'UserDefinedDir': 'E:/BS5_CN'}}}),
        )
        fs.create_file('E:/BS5_CN/bluestacks.conf', contents='bst.instance.cn.status.adb_port="5565"\n')
        assert [i.serial for i in windows.iter_instances(_bluestacks5_emu())] == ['127.0.0.1:5565']

    def test_bluestacks5_without_config(self, fs, monkeypatch):
        monkeypatch.setattr(windows, 'winreg', FakeWinreg({}))
        assert list(windows.iter_instances(_bluestacks5_emu())) == []

    def test_ldplayer_instances(self, fs):
        """Ports are derived from the instance number, 5555 + 2 * N."""
        fs.create_dir('E:/LDPlayer9/vms/leidian0')
        fs.create_dir('E:/LDPlayer9/vms/leidian3')
        fs.create_dir('E:/LDPlayer9/vms/not_leidian')
        assert [str(x) for x in windows.iter_instances(_ldplayer_emu(9))] == [
            'LDPlayer9(serial="127.0.0.1:5555", name="leidian0", path="E:/LDPlayer9/dnplayer.exe")',
            'LDPlayer9(serial="127.0.0.1:5561", name="leidian3", path="E:/LDPlayer9/dnplayer.exe")',
        ]

    def test_mumu_player_has_a_single_instance(self):
        assert [str(x) for x in windows.iter_instances(_mumu_emu())] == [
            'MuMuPlayer(serial="127.0.0.1:7555", name="", '
            'path="E:/MuMu/emulator/nemu/EmulatorShell/NemuPlayer.exe")'
        ]

    def test_mumu_player_x_instances(self, fs):
        fs.create_dir('E:/MuMu9/emulator/nemu9/vms/nemu-12.0-x64-default')
        fs.create_file(
            'E:/MuMu9/emulator/nemu9/vms/nemu-12.0-x64-default/nemu-12.0-x64-default.nemu',
            contents=VBOX_FORWARDING.format(host=16416),
        )
        assert [(i.serial, i.name) for i in windows.iter_instances(_mumu_x_emu())] == [
            ('127.0.0.1:16416', 'nemu-12.0-x64-default'),
        ]

    def test_mumu_player_12_uses_the_forwarding_record(self, fs):
        fs.create_dir('E:/MuMuPlayer-12.0/vms/MuMuPlayer-12.0-1')
        fs.create_file(
            'E:/MuMuPlayer-12.0/vms/MuMuPlayer-12.0-1/MuMuPlayer-12.0-1.nemu',
            contents=VBOX_FORWARDING.format(host=20001),
        )
        assert [(i.serial, i.name) for i in windows.iter_instances(_mumu_12_emu())] == [
            ('127.0.0.1:20001', 'MuMuPlayer-12.0-1'),
        ]

    def test_mumu_player_12_falls_back_to_the_instance_number(self, fs):
        """MuMu12 v4.0.4 has no forwarding record, the port comes from the name."""
        fs.create_dir('E:/MuMuPlayer-12.0/vms/MuMuPlayer-12.0-2')
        fs.create_file(
            'E:/MuMuPlayer-12.0/vms/MuMuPlayer-12.0-2/MuMuPlayer-12.0-2.nemu',
            contents='<VirtualBox></VirtualBox>',
        )
        assert [(i.serial, i.name) for i in windows.iter_instances(_mumu_12_emu())] == [
            ('127.0.0.1:16448', 'MuMuPlayer-12.0-2'),
        ]

    def test_mumu_player_12_without_a_nemu_file_yields_nothing(self, fs):
        fs.create_dir('E:/MuMuPlayer-12.0/vms/MuMuPlayer-12.0-2')
        assert list(windows.iter_instances(_mumu_12_emu())) == []

    def test_memu_instances(self, fs):
        fs.create_dir('E:/Microvirt/MEmu/MemuHyperv VMs/MEmu')
        fs.create_file(
            'E:/Microvirt/MEmu/MemuHyperv VMs/MEmu/MEmu.memu',
            contents=VBOX_FORWARDING.format(host=21503),
        )
        assert [(i.serial, i.name) for i in windows.iter_instances(_memu_emu())] == [
            ('127.0.0.1:21503', 'MEmu'),
        ]

    def test_list_instances_returns_a_list(self, fs):
        fs.create_dir('E:/LDPlayer9/vms/leidian0')
        instances = list_instances(_ldplayer_emu(9))
        assert isinstance(instances, list)
        assert all(isinstance(x, EmulatorInstance) for x in instances)
        assert [x.serial for x in instances] == ['127.0.0.1:5555']


class TestAdbBinaries:
    def test_adb_next_to_the_emulator(self, fs):
        """Every emulator ships an adb.exe next to its own executable."""
        fs.create_file('E:/LDPlayer9/adb.exe')
        assert [str(x) for x in windows.iter_adb_binaries(_ldplayer_emu(9))] == ['E:/LDPlayer9/adb.exe']

    def test_nox_uses_its_own_adb(self, fs):
        fs.create_file('E:/Nox/bin/nox_adb.exe')
        fs.create_file('E:/Nox/bin/adb.exe')
        assert [str(x) for x in windows.iter_adb_binaries(_nox_emu())] == [
            'E:/Nox/bin/nox_adb.exe',
            'E:/Nox/bin/adb.exe',
        ]

    @pytest.mark.parametrize("emutype", EmuType.NoxPlayerFamily)
    def test_every_nox_family_member_uses_its_own_adb(self, fs, emutype):
        """Every member of the family is matched by the family check."""
        emu = EmuStr.new(NOX_PATHS[emutype])
        assert emu.emutype() in EmuType.NoxPlayerFamily
        adb = emu.with_name('nox_adb.exe')
        fs.create_file(adb)
        assert [str(x) for x in windows.iter_adb_binaries(emu)] == [str(adb)]

    def test_mumu_uses_adb_server_of_vmonitor(self, fs):
        fs.create_file('E:/MuMu9/emulator/nemu9/vmonitor/bin/adb_server.exe')
        fs.create_file('E:/MuMu9/emulator/nemu9/EmulatorShell/adb.exe')
        assert [str(x) for x in windows.iter_adb_binaries(_mumu_x_emu())] == [
            'E:/MuMu9/emulator/nemu9/vmonitor/bin/adb_server.exe',
            'E:/MuMu9/emulator/nemu9/EmulatorShell/adb.exe',
        ]

    @pytest.mark.parametrize("emu", [
        _mumu_emu(),
        _mumu_x_emu(),
        _mumu_12_emu(),
    ], ids=['MuMuPlayer', 'MuMuPlayerX', 'MuMuPlayer12'])
    def test_every_mumu_family_member_uses_adb_server(self, fs, emu):
        assert emu.emutype() in EmuType.MuMuPlayerFamily
        exe = emu.uppath(2).joinpath('vmonitor/bin/adb_server.exe')
        fs.create_file(exe)
        assert exe in [str(x) for x in windows.iter_adb_binaries(emu)]

    def test_a_non_emulator_is_not_matched(self, fs):
        """An empty emutype matches no member of any family."""
        fs.create_file('C:/Windows/system32/nox_adb.exe')
        fs.create_file('C:/Windows/system32/adb.exe')
        emu = EmuStr.new('C:/Windows/system32/svchost.exe')
        assert emu.emutype() == ''
        assert [str(x) for x in windows.iter_adb_binaries(emu)] == ['C:/Windows/system32/adb.exe']

    def test_missing_binaries_yield_nothing(self):
        assert list(windows.iter_adb_binaries(_ldplayer_emu(9))) == []

    def test_list_adb_binaries_returns_a_list(self, fs):
        fs.create_file('E:/LDPlayer9/adb.exe')
        binaries = list_adb_binaries(_ldplayer_emu(9))
        assert isinstance(binaries, list)
        assert [str(x) for x in binaries] == ['E:/LDPlayer9/adb.exe']


class TestSearchMethods:
    @staticmethod
    def _search(monkeypatch):
        search = EmulatorSearchWindows()
        monkeypatch.setattr(search, 'path_exists', lambda path: True)
        return search

    @pytest.mark.parametrize("name, iterator", [
        ('search_user_assist', 'iter_user_assist'),
        ('search_mui_cache', 'iter_mui_cache'),
        ('search_known_registry', 'iter_known_registry'),
    ])
    def test_registry_searches_keep_existing_emulators(self, name, iterator, monkeypatch):
        search = self._search(monkeypatch)
        monkeypatch.setattr(windows, iterator, lambda: [
            EmuStr.new('E:/LDPlayer9/dnplayer.exe'),
            EmuStr.new('C:/Windows/system32/svchost.exe'),
        ])
        assert {str(x) for x in getattr(search, name)()} == {'E:/LDPlayer9/dnplayer.exe'}

    @pytest.mark.parametrize("name, iterator", [
        ('search_user_assist', 'iter_user_assist'),
        ('search_mui_cache', 'iter_mui_cache'),
        ('search_known_registry', 'iter_known_registry'),
    ])
    def test_registry_searches_drop_removed_emulators(self, name, iterator, monkeypatch):
        """A registry record of an uninstalled emulator must not be reported."""
        search = EmulatorSearchWindows()
        monkeypatch.setattr(search, 'path_exists', lambda path: False)
        monkeypatch.setattr(windows, iterator, lambda: [EmuStr.new('E:/LDPlayer9/dnplayer.exe')])
        assert getattr(search, name)() == set()

    def test_search_running_emulator_normalizes_the_cmdline(self, monkeypatch):
        """A backslash path from the OS is normalized before emutype() parses it."""
        search = self._search(monkeypatch)
        monkeypatch.setattr(windows, 'process_iter', lambda: [
            (1000, [r'E:\MuMuPlayer-12.0\shell\MuMuPlayer.exe', '--option']),
            (1001, [r'C:\Program Files\BlueStacks_nxt\HD-Player.exe']),
            (1002, [r'C:\Windows\system32\svchost.exe', '-k', 'netsvcs']),
        ])
        assert {str(x) for x in search.search_running_emulator()} == {
            'E:/MuMuPlayer-12.0/shell/MuMuPlayer.exe',
            'C:/Program Files/BlueStacks_nxt/HD-Player.exe',
        }

    def test_search_running_emulator_of_no_emulator(self, monkeypatch):
        search = self._search(monkeypatch)
        monkeypatch.setattr(windows, 'process_iter', lambda: [
            (1000, [r'C:\Windows\system32\conhost.exe']),
        ])
        assert search.search_running_emulator() == set()

    def test_search_uninstall_registry_scans_the_install_folder(self, fs, monkeypatch):
        monkeypatch.setattr(
            windows, 'iter_uninstall_registry',
            lambda: [EmuStr.new('E:/LDPlayer9/uninstall/dnuninst.exe')],
        )
        fs.create_file('E:/LDPlayer9/uninstall/helper.exe')
        fs.create_file('E:/LDPlayer9/uninstall/dnplayer.exe')
        assert {str(x) for x in EmulatorSearchWindows.search_uninstall_registry()} == {
            'E:/LDPlayer9/uninstall/dnplayer.exe'
        }

    def test_search_uninstall_registry_scans_the_parent_folder(self, fs, monkeypatch):
        """MEmu keeps its executable one level above its uninstaller."""
        monkeypatch.setattr(
            windows, 'iter_uninstall_registry',
            lambda: [EmuStr.new('E:/Microvirt/MEmu/uninstall/uninstall.exe')],
        )
        fs.create_file('E:/Microvirt/MEmu/MEmu.exe')
        assert {str(x) for x in EmulatorSearchWindows.search_uninstall_registry()} == {
            'E:/Microvirt/MEmu/MEmu.exe'
        }

    def test_search_uninstall_registry_scans_the_shell_folder(self, fs, monkeypatch):
        """MuMu12 keeps MuMuPlayer.exe in the `shell` folder of its root."""
        monkeypatch.setattr(
            windows, 'iter_uninstall_registry',
            lambda: [EmuStr.new('E:/MuMuPlayer-12.0/uninstall.exe')],
        )
        fs.create_file('E:/MuMuPlayer-12.0/shell/MuMuPlayer.exe')
        assert {str(x) for x in EmulatorSearchWindows.search_uninstall_registry()} == {
            'E:/MuMuPlayer-12.0/shell/MuMuPlayer.exe'
        }

    def test_search_uninstall_registry_scans_the_emulator_shell_folder(self, fs, monkeypatch):
        """MuMu9 keeps nemuplayer.exe in the `EmulatorShell` folder."""
        monkeypatch.setattr(
            windows, 'iter_uninstall_registry',
            lambda: [EmuStr.new('E:/MuMu9/emulator/nemu9/uninstall.exe')],
        )
        fs.create_file('E:/MuMu9/emulator/nemu9/EmulatorShell/nemuplayer.exe')
        assert {str(x) for x in EmulatorSearchWindows.search_uninstall_registry()} == {
            'E:/MuMu9/emulator/nemu9/EmulatorShell/nemuplayer.exe'
        }

    def test_search_uninstall_registry_probes_no_missing_folder(self, fs, monkeypatch):
        """A missing sub folder is not scanned, the listing already knows it."""
        monkeypatch.setattr(
            windows, 'iter_uninstall_registry',
            lambda: [EmuStr.new('E:/LDPlayer9/uninstall/dnuninst.exe')],
        )
        fs.create_file('E:/LDPlayer9/uninstall/dnplayer.exe')

        real_scandir = os.scandir
        scanned = []

        def counting_scandir(path):
            scanned.append(str(path))
            return real_scandir(path)

        monkeypatch.setattr(os, 'scandir', counting_scandir)
        EmulatorSearchWindows.search_uninstall_registry()

        assert not [path for path in scanned if path.endswith(('EmulatorShell', 'shell'))], scanned

    def test_search_uninstall_registry_without_uninstaller(self, monkeypatch):
        monkeypatch.setattr(windows, 'iter_uninstall_registry', lambda: [])
        assert EmulatorSearchWindows.search_uninstall_registry() == set()


class TestAggregates:
    @staticmethod
    def _patched_search(monkeypatch, results):
        search = EmulatorSearchWindows()
        for name, value in results.items():
            monkeypatch.setattr(search, name, lambda value=value: value)
        return search

    def test_all_emulators_merges_every_source(self, monkeypatch):
        search = self._patched_search(monkeypatch, {
            'search_running_emulator': {EmuStr.new('E:/LDPlayer9/dnplayer.exe')},
            'search_uninstall_registry': {EmuStr.new('E:/LDPlayer9/dnplayer.exe')},
            'search_mui_cache': {EmuStr.new('E:/MuMuPlayer-12.0/shell/MuMuPlayer.exe')},
            'search_user_assist': {EmuStr.new('E:/Nox/bin/Nox.exe')},
            'search_known_registry': set(),
        })
        assert [str(x) for x in search.all_emulators] == [
            'E:/LDPlayer9/dnplayer.exe',
            'E:/MuMuPlayer-12.0/shell/MuMuPlayer.exe',
            'E:/Nox/bin/Nox.exe',
        ]

    def test_all_emulators_dedups_case_insensitively(self, monkeypatch):
        search = self._patched_search(monkeypatch, {
            'search_running_emulator': {'e:/ldplayer9/dnplayer.exe'},
            'search_uninstall_registry': {'E:/LDPlayer9/dnplayer.exe'},
            'search_mui_cache': set(),
            'search_user_assist': set(),
            'search_known_registry': set(),
        })
        assert [str(x) for x in search.all_emulators] == ['E:/LDPlayer9/dnplayer.exe']

    def test_all_emulator_instances_uses_every_emulator(self, monkeypatch):
        search = EmulatorSearchWindows()
        search.__dict__['all_emulators'] = [
            EmuStr.new('E:/LDPlayer9/dnplayer.exe'),
            EmuStr.new('E:/Nox/bin/Nox.exe'),
        ]
        monkeypatch.setattr(windows, 'list_instances', lambda emu: [
            EmulatorInstance(serial=f'127.0.0.1:{emu.name}', name=emu.name, path=emu)
        ])
        # Sorted by str(), the LDPlayer instance comes before the Nox one
        assert [(i.serial, i.name) for i in search.all_emulator_instances] == [
            ('127.0.0.1:dnplayer.exe', 'dnplayer.exe'),
            ('127.0.0.1:Nox.exe', 'Nox.exe'),
        ]

    def test_all_adb_binaries_uses_every_emulator(self, monkeypatch):
        search = EmulatorSearchWindows()
        search.__dict__['all_emulators'] = [EmuStr.new('E:/LDPlayer9/dnplayer.exe')]
        monkeypatch.setattr(windows, 'list_adb_binaries', lambda emu: [emu.with_name('adb.exe')])
        assert [str(x) for x in search.all_adb_binaries] == ['E:/LDPlayer9/adb.exe']

    def test_cache_is_reused_and_can_be_cleared(self, monkeypatch):
        search = EmulatorSearchWindows()
        search.__dict__['all_emulators'] = [EmuStr.new('E:/LDPlayer9/dnplayer.exe')]
        assert search.all_emulators == ['E:/LDPlayer9/dnplayer.exe']
        search.clear_emulator_cache()
        assert 'all_emulators' not in search.__dict__


class TestRealMachine:
    """Runs on the real machine only to prove that nothing raises.

    The emulators installed on the machine (none, one or many) are not
    asserted: the tests above cover the parsing with mocked data.
    """

    def test_searching_does_not_raise(self):
        search = EmulatorSearchWindows()
        emulators = search.all_emulators
        assert isinstance(emulators, list)
        for emu in emulators:
            # Everything reported by the search must be a recognized emulator
            assert isinstance(emu, EmuStr)
            assert emu.emutype()
            assert isinstance(list(windows.iter_instances(emu)), list)
            assert isinstance(list(windows.iter_adb_binaries(emu)), list)

        assert isinstance(search.all_emulator_instances, list)
        assert isinstance(search.all_adb_binaries, list)
        assert isinstance(search.all_emulator_serials, list)
