import pytest

from alasio.device.search.base import (
    EmulatorInstance, EmulatorSearchBase, EmuStr, EmuType, flatten_list, remove_duplicated_path, vbox_file_to_serial
)
from alasio.ext.path import PathStr
from alasio.testing.filesystem import fs  # noqa: F401

DATA = PathStr.new(__file__).uppath().joinpath('data')


# The config files of a real Nox and a real MuMuPlayer 12 installation,
# captured from the machine and stripped of the machine UUID, the MAC addresses
# and the user name. They are read once at import time so the tests keep the
# in-memory filesystem fixture for the reads under test.
def _fixture(name):
    with open(DATA.joinpath(name), encoding='utf-8') as f:
        return f.read()


NOX_VBOX = _fixture('nox.vbox')
MUMU_NEMU = _fixture('mumu12.nemu')


class TestVboxFileToSerial:
    def test_nox_config(self, fs):
        """A real Nox config forwards adb on `port2`, host port 62001."""
        fs.create_file('/vms/nox/nox.vbox', contents=NOX_VBOX)
        assert vbox_file_to_serial('/vms/nox/nox.vbox') == '127.0.0.1:62001'

    def test_mumu_12_config(self, fs):
        """A real MuMu 12 config has two adb forwardings, the first one wins.

        ADB_PORT is 16384, ADB_PORT_OLD is 7555, both forward guest port 5555.
        """
        fs.create_file('/vms/MuMuPlayer-12.0-0/MuMuPlayer-12.0-0.nemu', contents=MUMU_NEMU)
        assert vbox_file_to_serial('/vms/MuMuPlayer-12.0-0/MuMuPlayer-12.0-0.nemu') == '127.0.0.1:16384'

    def test_forwarding_of_another_guest_port_is_ignored(self, fs):
        content = NOX_VBOX.replace('guestport="5555"', 'guestport="5556"')
        fs.create_file('/vms/other.vbox', contents=content)
        assert vbox_file_to_serial('/vms/other.vbox') == ''

    def test_missing_forwarding(self, fs):
        fs.create_file('/vms/empty.vbox', contents='<VirtualBox></VirtualBox>')
        assert vbox_file_to_serial('/vms/empty.vbox') == ''

    def test_several_forwardings_take_the_first_match(self, fs):
        content = (
            '<Forwarding name="port1" proto="1" hostip="127.0.0.1" '
            'hostport="62001" guestport="5555"/>\n'
            '<Forwarding name="port2" proto="1" hostip="127.0.0.1" '
            'hostport="62002" guestport="5555"/>'
        )
        fs.create_file('/vms/two.vbox', contents=content)
        assert vbox_file_to_serial('/vms/two.vbox') == '127.0.0.1:62001'

    def test_missing_file(self):
        assert vbox_file_to_serial('/not/exists.vbox') == ''

    def test_undecodable_content_is_ignored(self, fs):
        """errors='ignore' keeps a broken config from raising UnicodeDecodeError."""
        fs.create_file('/vms/broken.vbox', contents=b'\xff\xfe' + NOX_VBOX.encode())
        assert vbox_file_to_serial('/vms/broken.vbox') == '127.0.0.1:62001'


class TestRemoveDuplicatedPath:
    def test_case_insensitive_dedup(self):
        assert remove_duplicated_path(['E:/A/x.exe', 'e:/a/x.exe']) == ['E:/A/x.exe']

    def test_order_follows_sorting(self):
        result = remove_duplicated_path(['E:/b.exe', 'E:/a.exe', 'E:/b.exe'])
        assert result == ['E:/a.exe', 'E:/b.exe']

    def test_distinct_paths_are_kept(self):
        paths = ['E:/a.exe', 'E:/b.exe', 'E:/c.exe']
        assert remove_duplicated_path(paths) == paths

    @pytest.mark.parametrize("paths", [[], ['E:/only.exe']])
    def test_short_inputs(self, paths):
        assert remove_duplicated_path(paths) == paths


class TestFlattenList:
    def test_flattens_one_level(self):
        assert flatten_list([[1, 2], [3], []]) == [1, 2, 3]

    def test_empty(self):
        assert flatten_list([]) == []

    def test_items_keep_their_order(self):
        assert flatten_list([['b', 'a'], ['d', 'c']]) == ['b', 'a', 'd', 'c']


class TestEmuType:
    def test_families(self):
        assert EmuType.NoxPlayerFamily == [EmuType.NoxPlayer, EmuType.NoxPlayer64]
        assert EmuType.LDPlayerFamily == [
            EmuType.LDPlayer3,
            EmuType.LDPlayer4,
            EmuType.LDPlayer9,
            EmuType.LDPlayer14,
        ]
        assert EmuType.MuMuPlayerFamily == [
            EmuType.MuMuPlayer,
            EmuType.MuMuPlayerX,
            EmuType.MuMuPlayer12,
        ]
        assert EmuType.BlueStacksFamily == [EmuType.BlueStacks4, EmuType.BlueStacks5]


class TestEmuStrEmutype:
    """emutype() splits on '/', so entry points must normalize separators first.

    Paths coming from the OS (process cmdline, registry values) use '\\' on
    Windows; EmuStr.new() is the supported way to feed them in.
    """

    @pytest.mark.parametrize("path, expected", [
        (r'C:\Program Files\BlueStacks\HD-Player.exe', EmuType.BlueStacks4),
        (r'C:\Program Files\BlueStacks_nxt\HD-Player.exe', EmuType.BlueStacks5),
        (r'C:\Program Files\Nox\bin\Nox.exe', EmuType.NoxPlayer),
        (r'C:\Program Files\Nox64\bin\Nox.exe', EmuType.NoxPlayer64),
        (r'E:\LDPlayer9\dnplayer.exe', EmuType.LDPlayer9),
        (r'E:\LDPlayer14\leidian\LDPlayer14\dnplayer.exe', EmuType.LDPlayer14),
        (r'E:\MuMuPlayer-12.0\shell\MuMuPlayer.exe', EmuType.MuMuPlayer12),
        (r'E:\MuMu\emulator\nemu\EmulatorShell\NemuPlayer.exe', EmuType.MuMuPlayer),
        (r'E:\MuMu9\emulator\nemu9\EmulatorShell\NemuPlayer.exe', EmuType.MuMuPlayerX),
        (r'E:\Microvirt\MEmu\MEmu.exe', EmuType.MEmuPlayer),
    ])
    def test_backslash_path_is_recognized_after_new(self, path, expected):
        """Windows native paths are recognized once normalized by EmuStr.new()."""
        assert EmuStr.new(path).emutype() == expected

    def test_normalized_path_keeps_the_same_result(self):
        """Already normalized paths give the same result, no double normalization."""
        for path in (
            'C:/Program Files/BlueStacks_nxt/HD-Player.exe',
            'E:/LDPlayer9/dnplayer.exe',
            'E:/MuMuPlayer-12.0/shell/MuMuPlayer.exe',
        ):
            assert EmuStr.new(path).emutype() == EmuStr.new(EmuStr.new(path)).emutype()

    def test_a_raw_path_is_not_recognized(self):
        """The contract of the un-normalized constructor, kept explicit.

        EmuStr(path) does not normalize separators, so every entry point that
        takes a path from the OS has to use EmuStr.new() instead.
        """
        assert EmuStr(r'E:\LDPlayer9\dnplayer.exe').emutype() == ''
        assert EmuStr.new(r'E:\LDPlayer9\dnplayer.exe').emutype() == EmuType.LDPlayer9

    @pytest.mark.parametrize("path, expected", [
        ('C:/Anything/Bluestacks.exe', EmuType.BlueStacks4),
        ('C:/Anything/HD-Player.exe', EmuType.BlueStacks5),
        ('C:/Anything/dnplayer.exe', EmuType.LDPlayer3),
        ('C:/Anything/nemuplayer.exe', EmuType.MuMuPlayer),
        ('C:/Anything/mumuplayer.exe', EmuType.MuMuPlayer12),
        ('C:/Anything/MuMuNxMain.exe', EmuType.MuMuPlayer12),
        ('C:/Anything/MEmu.exe', EmuType.MEmuPlayer),
    ])
    def test_unknown_folder_falls_back_to_a_default(self, path, expected):
        """A known executable name is enough, the folder only refines the type."""
        assert EmuStr.new(path).emutype() == expected

    @pytest.mark.parametrize("path", [
        'C:/Windows/system32/svchost.exe',
        'C:/Windows/system32/conhost.exe',
        'E:/LDPlayer9/adb.exe',
        'E:/LDPlayer9/ldconsole.exe',
        '',
    ])
    def test_non_emulator_paths(self, path):
        assert EmuStr.new(path).emutype() == ''

    def test_case_insensitive(self):
        assert EmuStr.new('C:/X/NOX.EXE').emutype() == EmuType.NoxPlayer
        assert EmuStr.new('C:/X/DNPLAYER.EXE').emutype() == EmuType.LDPlayer3


class TestEmuStrEquality:
    """A path is a path, the emulator type is compared after `emutype()`.

    `emutype()` returns a plain str, so a single type is compared with `==`:
        emutype = emu.emutype()
        if emutype == EmuType.NoxPlayer: ...
    A family is a plain list of those strings, so its members are matched by
    membership, keeping the member list declared in one place:
        if emutype in EmuType.NoxPlayerFamily: ...
    """

    @staticmethod
    def _emu(path='E:/LDPlayer9/dnplayer.exe'):
        return EmuStr.new(path)

    def test_a_path_is_not_equal_to_an_emulator_type(self):
        assert self._emu() != EmuType.LDPlayer9
        assert self._emu() != 'LDPlayer9'

    def test_a_path_is_not_equal_to_a_family(self):
        assert self._emu('E:/LDPlayer14/leidian/LDPlayer14/dnplayer.exe') != EmuType.LDPlayerFamily
        assert self._emu('E:/Nox64/bin/Nox.exe') != EmuType.NoxPlayerFamily

    def test_the_emutype_compares_with_a_type_constant(self):
        assert self._emu().emutype() == EmuType.LDPlayer9
        assert self._emu('E:/Nox64/bin/Nox.exe').emutype() == EmuType.NoxPlayer64
        assert self._emu().emutype() != EmuType.NoxPlayer

    @pytest.mark.parametrize("path, family", [
        ('E:/LDPlayer14/leidian/LDPlayer14/dnplayer.exe', EmuType.LDPlayerFamily),
        ('E:/Nox64/bin/Nox.exe', EmuType.NoxPlayerFamily),
        ('E:/MuMuPlayer-12.0/shell/MuMuPlayer.exe', EmuType.MuMuPlayerFamily),
        ('C:/Program Files/BlueStacks_nxt/HD-Player.exe', EmuType.BlueStacksFamily),
    ])
    def test_the_emutype_is_a_member_of_its_family(self, path, family):
        assert self._emu(path).emutype() in family

    def test_an_emutype_of_another_family_is_not_a_member(self):
        assert self._emu().emutype() not in EmuType.NoxPlayerFamily
        assert self._emu('E:/Nox64/bin/Nox.exe').emutype() not in EmuType.LDPlayerFamily
        assert self._emu('C:/Windows/system32/svchost.exe').emutype() not in EmuType.NoxPlayerFamily

    def test_equality_with_a_path_is_a_string_comparison(self):
        emu = self._emu()
        assert emu == 'E:/LDPlayer9/dnplayer.exe'
        assert emu == EmuStr.new('E:/LDPlayer9/dnplayer.exe')
        assert emu != 'E:/LDPlayer9/adb.exe'

    def test_hash_stays_the_hash_of_str(self):
        emu = self._emu()
        assert hash(emu) == hash('E:/LDPlayer9/dnplayer.exe')
        assert emu in {'E:/LDPlayer9/dnplayer.exe'}


class TestEmuStrIterSingleFromMulti:
    @pytest.mark.parametrize("path, expected", [
        (
            'C:/BlueStacks/HD-MultiInstanceManager.exe',
            ['C:/BlueStacks/HD-Player.exe', 'C:/BlueStacks/Bluestacks.exe'],
        ),
        ('C:/Nox/MultiPlayerManager.exe', ['C:/Nox/Nox.exe']),
        ('C:/LD/dnmultiplayer.exe', ['C:/LD/dnplayer.exe']),
        ('C:/MuMu/NemuMultiPlayer.exe', ['C:/MuMu/NemuPlayer.exe']),
        ('C:/MuMu/MuMuMultiPlayer.exe', ['C:/MuMu/MuMuPlayer.exe']),
        ('C:/MuMu/MuMuManager.exe', ['C:/MuMu/MuMuPlayer.exe']),
        ('C:/MEmu/MEmuConsole.exe', ['C:/MEmu/MEmu.exe']),
    ])
    def test_managers_are_converted_to_their_single_instance(self, path, expected):
        assert [str(x) for x in EmuStr.new(path).iter_single_from_multi()] == expected

    def test_a_single_instance_yields_nothing(self):
        assert list(EmuStr.new('E:/LDPlayer9/dnplayer.exe').iter_single_from_multi()) == []


class TestEmuStrSingleToConsole:
    @pytest.mark.parametrize("path, expected", [
        ('C:/MuMu/shell/MuMuPlayer.exe', 'C:/MuMu/shell/MuMuManager.exe'),
        ('C:/MuMu/shell/MuMuNxMain.exe', 'C:/MuMu/shell/MuMuManager.exe'),
        ('C:/LD/LDPlayer.exe', 'C:/LD/ldconsole.exe'),
        ('C:/LD/dnplayer.exe', 'C:/LD/ldconsole.exe'),
        ('C:/BS/Bluestacks.exe', 'C:/BS/bsconsole.exe'),
        ('C:/MEmu/MEmu.exe', 'C:/MEmu/memuc.exe'),
        ('C:/Foo/Bar.exe', ''),
        ('C:/Foo/mumuplayer.exe', ''),
    ])
    def test_console_resolution(self, path, expected):
        assert EmuStr.new(path).single_to_console() == expected


class TestEmulatorInstance:
    @staticmethod
    def _instance(name='a', serial='127.0.0.1:5555', path='E:/LDPlayer9/dnplayer.exe'):
        return EmulatorInstance(serial=serial, name=name, path=EmuStr.new(path))

    def test_equality_with_an_emulator_type(self):
        instance = self._instance()
        assert instance == EmuType.LDPlayer9
        assert instance != EmuType.NoxPlayer

    def test_equality_with_a_family(self):
        instance = self._instance()
        assert instance == [EmuType.LDPlayer9, EmuType.MuMuPlayer12]
        assert not instance == [EmuType.NoxPlayer]

    def test_equality_with_another_instance(self):
        """Instances are compared by their fields, which keeps them hashable."""
        assert self._instance() == self._instance()
        assert self._instance(name='a') != self._instance(name='b')
        assert self._instance(serial='127.0.0.1:5555') != self._instance(serial='127.0.0.1:5557')
        assert self._instance() != self._instance(path='E:/Nox/bin/Nox.exe')

    def test_hash_follows_str(self):
        instance = self._instance()
        assert hash(instance) == hash(str(instance))

    def test_equal_instances_have_equal_hashes(self):
        assert hash(self._instance()) == hash(self._instance())

    def test_set_keeps_the_instances_of_one_emulator(self):
        """Multi-instance emulators must keep one entry per instance."""
        instances = {
            self._instance(name='leidian0', serial='127.0.0.1:5555'),
            self._instance(name='leidian1', serial='127.0.0.1:5557'),
            self._instance(name='leidian0', serial='127.0.0.1:5555'),  # duplicate
            self._instance(name='a', path='E:/Nox/bin/Nox.exe'),
        }
        assert len(instances) == 3

    def test_bool_is_always_true(self):
        assert bool(self._instance()) is True

    def test_str_reports_the_fields(self):
        assert str(self._instance()) == (
            'LDPlayer9(serial="127.0.0.1:5555", name="a", path="E:/LDPlayer9/dnplayer.exe")'
        )

    def test_emutype_is_cached_from_the_path(self):
        instance = self._instance()
        assert instance.emutype == EmuType.LDPlayer9
        assert 'emutype' in instance.__dict__

    @pytest.mark.parametrize("name, expected", [
        ('MuMuPlayer-12.0-3', 3),
        ('MuMuPlayerGlobal-12.0-0', 0),
        ('MuMuPlayer-15.0-2', 2),
        ('YXArkNights-12.0-1', 1),
        ('MuMuPlayer-12.0', None),
        ('', None),
    ])
    def test_mumu_player_12_id(self, name, expected):
        assert self._instance(name=name).MuMuPlayer12_id == expected

    @pytest.mark.parametrize("name, expected", [
        ('leidian0', 0),
        ('leidian12', 12),
        ('MuMuPlayer-12.0-3', None),
        ('', None),
    ])
    def test_ldplayer_id(self, name, expected):
        assert self._instance(name=name).LDPlayer_id == expected


class TestEmulatorSearchBase:
    def test_defaults_are_empty(self):
        base = EmulatorSearchBase()
        assert base.all_emulators == []
        assert base.all_emulator_instances == []
        assert base.all_adb_binaries == []

    def test_iter_running_emulator_yields_nothing(self):
        """The base implementation is a stub, platforms override it."""
        assert not EmulatorSearchBase.iter_running_emulator()

    def test_serials_include_the_emulator_pair(self):
        base = EmulatorSearchBase()
        base.__dict__['all_emulator_instances'] = [
            EmulatorInstance(
                serial='127.0.0.1:5555', name='leidian0', path=EmuStr.new('E:/LDPlayer9/dnplayer.exe')
            ),
            # A port outside of the adb range has no `emulator-xxxx` counterpart
            EmulatorInstance(
                serial='127.0.0.1:20001', name='x', path=EmuStr.new('E:/LDPlayer9/dnplayer.exe')
            ),
        ]
        assert base.all_emulator_serials == [
            '127.0.0.1:5555',
            'emulator-5554',
            '127.0.0.1:20001',
        ]

    def test_clear_emulator_cache_drops_every_cache(self):
        base = EmulatorSearchBase()
        base.__dict__['all_emulators'] = ['a']
        base.__dict__['all_emulator_instances'] = ['b']
        base.__dict__['all_emulator_serials'] = ['c']
        base.__dict__['all_adb_binaries'] = ['d']
        base.clear_emulator_cache()
        for attrname in (
            'all_emulators',
            'all_emulator_instances',
            'all_emulator_serials',
            'all_adb_binaries',
        ):
            assert attrname not in base.__dict__
        # The defaults are computed again after the cache is cleared
        assert base.all_emulators == []
        assert base.all_emulator_serials == []
