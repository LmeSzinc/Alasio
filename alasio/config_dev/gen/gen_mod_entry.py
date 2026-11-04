"""
Generator for the declaration of a mod: ``{path_config}/_index/mod_entry.yaml``
and the python module that hands the entry to the code of the mod,
``{path_config}/_index/mod_entry.py``.

The declaration is maintained like ``config/deploy.yaml``: the file is written
from :class:`ModEntryModel`, so its keys follow the order of the model, its
comments are the ``help`` annotations of the model, and its format is the one
of :class:`YamlConfig`. A key added to the model, or a help text edited in it,
reaches every mod the next time ``module.config.gen`` runs.

The values come from the entry the generation runs with, not from the file as
it is on the disk: the generation writes the declaration of the mod it
generates, whatever a half-edited file holds. A value the file declares is
kept, because it was read into the model first; a key the file omits is
written with the default of the model, so the declaration always shows every
key the framework knows.

The python module next to the declaration is the one place the code of a mod
creates its entry::

    from alasio.config.entry.mod_entry import ModEntry

    MOD_ENTRY = ModEntry.create(__file__, uppath=4)

Every module of the mod imports ``MOD_ENTRY`` instead of computing the root
from its own depth, so the layout of the mod is stated once. It is written for
a mod only: the entry of the library is a value of its code instead
(``ModEntry.alasio``), so the generation of the library writes the yaml alone.
The generator itself (``module/config/gen.py``) cannot use the module: it is
the call that creates the file on a mod that has none yet.

The writes are skipped when the content is already the one to write, like the
other generators: running the generation twice touches no file.

Usage:
    from alasio.config_dev.gen.gen_mod_entry import GenModEntry

    GenModEntry(entry).generate_mod_entry()
"""

from alasio.codegen.python import CodeGen
from alasio.config.entry.mod_entry import MOD_CONFIG, MOD_ENTRY_NAME
from alasio.config_dev.gen.gen_cross import CrossNavGenerator
from alasio.ext.cache import cached_property
from alasio.logger import logger


class GenModEntry(CrossNavGenerator):
    """Generator for {path_config}/_index/mod_entry.yaml and mod_entry.py."""

    @cached_property
    def mod_entry_file(self):
        """
        Returns:
            PathStr: {path_config}/_index/mod_entry.yaml
        """
        return self.entry.file

    @cached_property
    def mod_entry_py_file(self):
        """
        Returns:
            PathStr: {path_config}/_index/mod_entry.py
        """
        return self.path_config.joinpath('_index/mod_entry.py')

    def generate_mod_entry(self, gitadd=None):
        """
        Write the declaration of the mod and the module that exposes it.

        The module is written for a mod only: the entry of the library itself
        is a value of its code (`ModEntry.alasio`), it ships no module of its
        own.

        Args:
            gitadd (GitAdd | None): Stage the files that changed. Defaults to
                None, the files are only written

        Returns:
            bool: if any file was written
        """
        written = self.entry.write(gitadd=gitadd)
        if self.entry.path_config != MOD_CONFIG:
            return written
        return self.generate_mod_entry_py(gitadd=gitadd) or written

    def generate_mod_entry_py(self, gitadd=None):
        """
        Write {path_config}/_index/mod_entry.py, the entry of the mod.

        The file lives in the _index folder next to the declaration, so the
        root of the mod is 4 levels above it.

        Args:
            gitadd (GitAdd | None): Stage the file when it changed. Defaults to
                None, the file is only written

        Returns:
            bool: if write
        """
        gen = CodeGen()
        gen.FromImport('alasio.config.entry.mod_entry').Import('ModEntry')
        # The banner of a file of top-level assignments is a plain comment, see
        # CodeGen.CommentCodeGenInline: the class files use CommentCodeGen
        gen.CommentCodeGenInline('module.config.gen')
        gen.Empty()
        gen.Raw(f'{MOD_ENTRY_NAME} = ModEntry.create(__file__, uppath=4)')

        file = self.mod_entry_py_file
        op = gen.write(file, skip_same=True)
        if op:
            logger.info(f'Write file {file}')
            if gitadd:
                gitadd.stage_add(file)
        return op
