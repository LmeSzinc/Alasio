"""
The part every generator of the project shares: the file its text is written to.

A generator of alasio.codegen renders its text with generate_str() and writes it
with write(). The write is the same for the two generators the module has, a
python module and an html page, so it lives here and the two inherit it instead
of each writing the steps of its own:

    gen = HtmlGen()
    gen.P('hello')
    gen.write('out/index.html')   # True: the file was written

A generator is run again by every run of the job that publishes its output, and
a run that changes nothing must not touch the file: a publish record and a static
host then see the same bytes, and a watcher of the folder is not woken up for
nothing. So the file is read first and its content compared with the new one,
and only a different content is written, atomically, so a reader never sees half
a file.

A file that is not utf-8 text is not a file this module wrote (a leftover of
another tool, a file of another encoding): its content is read as missing and
the new text replaces it.
"""

from alasio.ext.path.atomic import atomic_read_text, atomic_write

__all__ = [
    'GenBase',
]


class GenBase:
    """
    A generator of a text, and the file it is written to.

    The subclass answers the text (generate_str) and the base writes it, so the
    steps of a write are written once for every generator of the project.
    """

    def generate_str(self):
        """
        Render the text of the generator.

        Returns:
            str: The text

        Raises:
            NotImplementedError: Always, the subclass renders the text
        """
        raise NotImplementedError

    def write(self, file='', gitadd=None, skip_same=True):
        """
        Write the text of the generator to a file.

        Args:
            file (str): Path of the file, the parent folder is created when it
                is missing
            gitadd (GitAdd, optional): Git add of the project, the file is
                staged when it was written. Defaults to None, nothing is staged.
                A git add whose truth value is False stages nothing either: the
                object says whether the files of a run are staged
            skip_same (bool): True to read the file first and skip the write
                when it already holds the text. This costs a read and saves a
                write, and the publish record of a rerun stays as it was. False
                to write whatever the file holds

        Returns:
            bool: True when the file was written

        Raises:
            OSError: If the file cannot be read or written
        """
        data = self.generate_str()
        if skip_same:
            try:
                old = atomic_read_text(file)
                old = old.replace('\r\n', '\n')
                if data == old:
                    return False
            except (FileNotFoundError, UnicodeDecodeError):
                # no file yet, or a file of another encoding: the text replaces it
                pass

        atomic_write(file, data)
        if gitadd:
            gitadd.stage_add(file)
        return True
