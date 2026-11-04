"""
Generator for ``{path_config}/_index/mod_entry.yaml``, the declaration of a mod.

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

The write is skipped when the content is already the one to write, like the
other generators: running the generation twice touches no file.

Usage:
    from alasio.config_dev.gen.gen_mod_entry import GenModEntry

    GenModEntry(entry).generate_mod_entry()
"""

from alasio.config_dev.gen.gen_cross import CrossNavGenerator
from alasio.ext.cache import cached_property


class GenModEntry(CrossNavGenerator):
    """Generator for {path_config}/_index/mod_entry.yaml."""

    @cached_property
    def mod_entry_file(self):
        """
        Returns:
            PathStr: {path_config}/_index/mod_entry.yaml
        """
        return self.entry.file

    def generate_mod_entry(self, gitadd=None):
        """
        Write the declaration of the mod, comments and format included.

        Args:
            gitadd (GitAdd | None): Stage the file when it changed. Defaults to
                None, the file is only written

        Returns:
            bool: if write
        """
        return self.entry.write(gitadd=gitadd)
