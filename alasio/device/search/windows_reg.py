import codecs
import re
import winreg
from dataclasses import dataclass

from alasio.device.search.base import EmuStr


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


def extract_uninstall_path(cmd):
    r"""
    Extract exe path from Windows UninstallString is like:
    C:\Program Files\BlueStacks_nxt\BlueStacksUninstaller.exe -tmp
    E:\ProgramFiles\AweSun\AweSun.exe
    "C:\Program Files (x86)\Everything/Uninstall.exe"
    "E:\ProgramFiles\Microvirt\MEmu/uninstall/uninstall.exe" -u
    C:\Windows\aaa.EXE -uninstall=\"D:/xxx/xxx.exe\"
    E:\ProgramFiles\PyCharm Community Edition 2023.3.7\bin/Uninstall.exe
    E:\ProgramFiles\Revit 2020\Setup\Setup.exe /P {xxx} /M RVT /LANG zh-CN

    Args:
        cmd (str):

    Returns:
        str:
    """
    try:
        cmd = cmd.strip()
    except (AttributeError, TypeError):
        # oops, not a string
        return ''
    if not cmd:
        return ''

    # Extract path in ""
    if cmd.startswith('"'):
        return cmd[1:].partition('"')[0]

    # Handle path with <space> but without ""
    parts = cmd.split(' ')
    if len(parts) == 1:
        return cmd
    valid_ext = ('.exe', '.msi', '.bat', '.com', '.cmd')
    end_index = 0
    for part in parts:
        end_index += 1
        if part.lower().endswith(valid_ext):
            return ' '.join(parts[:end_index])

    # fallback
    return cmd


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
    except OSError:
        return

    for folder in folders:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f'{path}\\{folder}\\Count') as reg:
                for key in list_reg(reg):
                    try:
                        key = codecs.decode(key.name, 'rot-13')
                    except ValueError:
                        # decode error
                        continue
                    # Skip those with hash
                    if regex_hash.search(key):
                        continue
                    file = EmuStr.new(key)
                    yield file
                    for single in file.iter_single_from_multi():
                        yield EmuStr.new(single)
        except OSError:
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
    except OSError:
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
            yield EmuStr.new(single)


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
    except OSError:
        pass
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as reg:
            root = winreg.QueryValueEx(reg, key)[0]
            return root
    except OSError:
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
        r'SOFTWARE\leidian\ldplayer9',
        r'SOFTWARE\leidian\ldplayer14',
    ]:
        file = get_install_dir_from_reg(path, 'InstallDir')
        if file:
            file = EmuStr.new(file) / 'dnplayer.exe'
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
        'leidian14',
        'Nemu',
        'Nemu9',
        'MuMuPlayer-12.0'
        'MuMuPlayer',
        'MuMuPlayer-12.0',
        'MuMu Player 12.0',
        'MEmu',
    ]
    for path in known_uninstall_registry_path:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as reg:
                software_list = list_key(reg)
        except OSError:
            continue
        for software in software_list:
            if software not in known_emulator_registry_name:
                continue
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f'{path}\\{software}') as software_reg:
                    uninstall = winreg.QueryValueEx(software_reg, 'UninstallString')[0]
            except OSError:
                continue

            uninstall = extract_uninstall_path(uninstall)
            if not uninstall:
                continue
            exe = EmuStr.new(uninstall)
            yield exe
