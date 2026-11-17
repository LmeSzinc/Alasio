"""
Tests for the release cleanup (alasio/deploy_dev/release/cleanup.py):

- ``cleanup_electron_locales``, the electron locale packs kept for the GUI
  languages;
- ``cleanup_electron``, the electron build cleaned with them.
"""
import os

import pytest

from alasio.config.entry.mod_entry import ModEntry
from alasio.deploy_dev.release.cleanup import cleanup_electron, cleanup_electron_locales
from alasio.ext.path import PathStr
from alasio.testing.filesystem import fs  # noqa: F401

# Root of the tests, an electron folder look-alike
ELECTRON = '/webapp'

# Locale packs of a real electron 22 distribution (a selection): a language
# translated as a whole has no region in its name, a language with several
# translations has one
PAKS = (
    'af.pak',
    'en-GB.pak',
    'en-US.pak',
    'es-419.pak',
    'es.pak',
    'ja.pak',
    'ko.pak',
    'zh-CN.pak',
    'zh-TW.pak',
)

# Doc files of the electron build, none of them is read at runtime
DOCS = ('LICENSE.electron.txt', 'LICENSES.chromium.html')

# Locale packs the languages of the library resolve to, the aliases included
LIBRARY_PAKS = ['en-US.pak', 'es.pak', 'ja.pak', 'zh-CN.pak', 'zh-TW.pak']


def create_electron(fake, paks=PAKS):
    """
    Create the folder layout of an electron build.

    Args:
        fake (FakeFilesystem): Active fake filesystem
        paks (tuple[str, ...]): Names of the locale packs to create
    """
    for pak in paks:
        fake.create_file(f'{ELECTRON}/locales/{pak}', contents=b'')
    for doc in DOCS:
        fake.create_file(f'{ELECTRON}/{doc}', contents=b'')


def kept_paks(fake):
    """
    Locale packs of the electron build that survived the cleanup.

    Args:
        fake (FakeFilesystem): Active fake filesystem

    Returns:
        list[str]: Sorted names of the pack files left in the locales folder
    """
    return sorted(os.listdir(f'{ELECTRON}/locales'))


class TestCleanupElectronLocales:
    @pytest.mark.parametrize('gui_language, kept', [
        # every language translated as a whole ships without a region
        ({'en-US': None}, ['en-US.pak']),
        ({'zh-CN': None}, ['zh-CN.pak']),
        ({'ja-JP': None}, ['ja.pak']),
        ({'es-ES': None}, ['es.pak']),
        # a language with several translations keeps its own region only
        ({'zh-TW': None}, ['zh-TW.pak']),
        # the tags of the frontend, both regional and whole
        (
            {'en-US': None, 'zh-CN': None, 'ja-JP': None, 'zh-TW': None, 'es-ES': None},
            LIBRARY_PAKS,
        ),
        # a pack that is not needed by a declared language is removed
        ({'en-US': None, 'zh-CN': None}, ['en-US.pak', 'zh-CN.pak']),
    ])
    def test_keeps_the_packs_of_the_gui_language(self, fs, gui_language, kept):
        """A declared language keeps its pack, its alias and nothing else."""
        create_electron(fs)
        cleanup_electron_locales(PathStr.new(ELECTRON), gui_language)
        assert kept_paks(fs) == kept

    def test_keeps_the_packs_of_the_library_by_default(self, fs):
        """Without a declared language, the languages of the library are kept."""
        create_electron(fs)
        cleanup_electron_locales(PathStr.new(ELECTRON))
        assert kept_paks(fs) == LIBRARY_PAKS

    def test_default_is_the_language_of_the_library(self):
        """The default set is the one the library declares, not a copy."""
        assert dict(ModEntry.alasio.gui_language) == {
            'zh-CN': None,
            'en-US': None,
            'ja-JP': None,
            'zh-TW': None,
            'es-ES': None,
        }

    def test_silent_when_every_language_is_served(self, fs, capsys):
        """A cleanup that keeps every declared language reports no missing pack."""
        create_electron(fs)
        cleanup_electron_locales(PathStr.new(ELECTRON), {'en-US': None, 'ja-JP': None})
        assert 'MISSING' not in capsys.readouterr().out

    def test_reports_a_language_without_pack(self, fs, capsys):
        """A GUI language the build cannot serve is reported, not silently dropped."""
        create_electron(fs, paks=('en-US.pak',))
        cleanup_electron_locales(PathStr.new(ELECTRON), {'en-US': None, 'ja-JP': None})
        out = capsys.readouterr().out
        assert 'MISSING locale pack of ja-JP' in out

    def test_empty_locales_reports_the_languages(self, fs, capsys):
        """An electron build without any locale pack cannot serve a language."""
        fs.create_dir(f'{ELECTRON}/locales')
        cleanup_electron_locales(PathStr.new(ELECTRON), {'en-US': None, 'ja-JP': None})
        out = capsys.readouterr().out
        assert 'MISSING locale pack of en-US, ja-JP' in out

    def test_missing_folder_is_ignored(self, fs, capsys):
        """A folder that is not an electron build has nothing to clean."""
        cleanup_electron_locales(PathStr.new('/missing'))
        assert not os.path.exists('/missing')
        assert capsys.readouterr().out == ''

    def test_folder_without_locales_is_ignored(self, fs, capsys):
        """A release that ships the shell only at its root has no toolkit/WebApp."""
        fs.create_dir(ELECTRON)
        cleanup_electron_locales(PathStr.new(ELECTRON))
        assert capsys.readouterr().out == ''


class TestCleanupElectron:
    def test_removes_the_doc_files(self, fs):
        """The licenses of the electron build are removed."""
        create_electron(fs)
        cleanup_electron(PathStr.new(ELECTRON))
        for doc in DOCS:
            assert not os.path.exists(f'{ELECTRON}/{doc}')

    def test_cleans_the_locales(self, fs):
        """The locale packs are pruned by the language the caller declares."""
        create_electron(fs)
        cleanup_electron(PathStr.new(ELECTRON), {'ja-JP': None})
        assert kept_paks(fs) == ['ja.pak']

    def test_cleans_the_locales_of_the_library_by_default(self, fs):
        """An electron build cleaned without a language keeps the library ones."""
        create_electron(fs)
        cleanup_electron(PathStr.new(ELECTRON))
        assert kept_paks(fs) == LIBRARY_PAKS

    def test_missing_folder_is_ignored(self, fs, capsys):
        """A folder that is not an electron build has nothing to clean."""
        cleanup_electron(PathStr.new('/missing'))
        assert not os.path.exists('/missing')
        assert capsys.readouterr().out == ''
