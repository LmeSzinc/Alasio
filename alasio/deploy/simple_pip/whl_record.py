import base64
import csv
import hashlib
import io

from alasio.ext.path.atomic import atomic_read_bytes
from alasio.ext.path.calc import to_posix


def sha256_checksum(content):
    """
    Compute the RECORD checksum of a file content, PEP 427.

    The checksum is the urlsafe base64 of the sha256 digest without padding,
    e.g. b'pip\\n' gives 'zuuue4knoyJ-UwPPXg8fezS7VCrXJQrAP7zeNuwvFQg'.

    Args:
        content (bytes): File content

    Returns:
        str: Checksum without the "sha256=" prefix
    """
    hash_sha256 = hashlib.sha256()
    hash_sha256.update(content)
    digest = hash_sha256.digest()
    return base64.urlsafe_b64encode(digest).decode('latin1').rstrip('=')


class RecordEntry:
    """
    A row of a RECORD file, PEP 376: path, hash and size of an installed file.

    Attributes:
        path (str): Path of the file relative to site-packages, posix style
        sha256 (str): "sha256=<checksum>", empty when no hash is recorded
        size (str): Size of the file in bytes, empty when no size is recorded
    """

    def __init__(self, path, sha256="", size=""):
        """
        Args:
            path (str): Path of the file relative to site-packages, posix style
            sha256 (str): "sha256=<checksum>". Defaults to "".
            size (str): Size of the file in bytes. Defaults to "".
        """
        self.path = path
        self.sha256 = sha256
        self.size = size

    def __repr__(self):
        return f"<RecordEntry {self.path},{self.sha256},{self.size}>"


class RecordManager:
    """
    Read, write and update the RECORD of a distribution, PEP 376.

    The RECORD lists every file the installation created, with the path
    relative to site-packages (files installed outside of it, e.g. scripts,
    are recorded with "../.."). pip reads the RECORD to uninstall a
    distribution, a missing RECORD makes pip refuse the uninstallation
    ("no RECORD file was found"), so an installer has to write it.

    Attributes:
        entries (dict[str, RecordEntry]): key is the path of the entry,
            the same as RecordEntry.path
    """

    def __init__(self):
        self.entries: "dict[str, RecordEntry]" = {}

    def load_bytes(self, content):
        """
        Load a RECORD from its file content.

        The RECORD is a csv file without a header, records written by other
        tools may have rows with one column only or with extra columns, the
        extra columns are ignored like pip does.

        Args:
            content (bytes): Content of the RECORD file, UTF-8 as required
                by PEP 376

        Raises:
            UnicodeDecodeError: If the content is not UTF-8
        """
        entries = {}
        text_content = content.decode('utf-8')
        f = io.StringIO(text_content)

        # RECORD is a csv file, but the fields are not quoted most of the time
        reader = csv.reader(f, delimiter=',', quoting=csv.QUOTE_MINIMAL)
        for row in reader:
            if len(row) >= 3:
                path = to_posix(row[0])
                entries[path] = RecordEntry(path, row[1], row[2])
            elif len(row) > 0:  # Some tools write rows with the path only, e.g. the RECORD itself
                path = to_posix(row[0])
                entries[path] = RecordEntry(path, "", "")

        self.entries = entries

    def dump_bytes(self):
        """
        Serialize the RECORD to its file content.

        The rows are sorted by the components of the path, the files of a
        directory stay together and the directory comes before the files
        named after it: "demo/aaa/name.py" before "demo/aaa-z.py" and
        "demo/aaaname.py". A plain sort of the raw strings interleaves them,
        "/" is 0x2f, after "-" and "." but before the digits and the letters.
        The order of the rows carries no meaning in the format, the sorting is
        only there to keep the output reproducible.

        Returns:
            bytes: Content of the RECORD file
        """
        # Sort by the components of the path to keep the output reproducible
        entries = sorted(self.entries.values(), key=lambda entry: entry.path.split('/'))
        self.entries = {entry.path: entry for entry in entries}

        f = io.StringIO()
        writer = csv.writer(
            f,
            delimiter=',',
            quotechar='"',
            quoting=csv.QUOTE_MINIMAL,
            lineterminator='\n'
        )

        for entry in self.entries.values():
            writer.writerow([entry.path, entry.sha256, entry.size])

        # ../../Scripts/flask.exe,sha256=S0-6lL0qtZxuaPnj-z8g9RYkziez-D0R8TkGAlDMJjM,106346
        # __pycache__/typing_extensions.cpython-38.pyc,,
        # flask/__init__.py,sha256=9ZCelLoNCpr6eSuLmYlzvbp12B3lrLgoN5U2UWk1vdo,2251
        # typing_extensions-4.13.2.dist-info/INSTALLER,sha256=zuuue4knoyJ-UwPPXg8fezS7VCrXJQrAP7zeNuwvFQg,4
        # typing_extensions.py,sha256=o48qcATlT6qQcRLzOvazSjPXHU0nCbw5LShwl0jrtag,172654
        return f.getvalue().encode('utf-8')

    def add_content(self, path, data):
        """
        Add an entry with the checksum and the size computed from the content.

        Args:
            path (str): Path of the file relative to site-packages
            data (bytes | None): Content of the file, None for a file with
                no recorded hash and size, e.g. the RECORD itself or a .pyc
                file generated at install time (pip does the same)
        """
        if data is None:
            sha256 = ""
            size = ""
        else:
            sha256 = f'sha256={sha256_checksum(data)}'
            size = str(len(data))

        path = to_posix(path)
        self.entries[path] = RecordEntry(path, sha256, size)

    def add_file(self, path, abspath):
        """
        Add an entry with the checksum and the size computed from a file.

        Args:
            path (str): Path of the file relative to site-packages
            abspath (str): Path of the file on disk
        """
        data = atomic_read_bytes(abspath)
        self.add_content(path, data)

    def iter_py_files(self):
        """
        Iter the .py entries, the files to compile to .pyc at install time.

        Yields:
            RecordEntry:
        """
        for path, entry in self.entries.items():
            if path.endswith('.py'):
                yield entry
