import base64
import csv
import hashlib
import io

from alasio.ext.path.atomic import atomic_read_bytes
from alasio.ext.path.calc import to_posix


def sha256_checksum(content):
    """
    计算文件的 sha256 哈希值（Base64 URL 编码并去掉填充）和大小
    符合 PEP 427 规范

    Args:
        content (bytes):

    Returns:
        str:
    """
    hash_sha256 = hashlib.sha256()
    hash_sha256.update(content)
    digest = hash_sha256.digest()
    return base64.urlsafe_b64encode(digest).decode('latin1').rstrip('=')


class RecordEntry:
    """代表 RECORD 文件中的一行记录"""

    def __init__(self, path, sha256="", size=""):
        """
        Args:
            path (str):
            sha256 (str): 格式通常为 sha256=xxx. Defaults to "".
            size (str): Defaults to "".
        """
        self.path = path
        self.sha256 = sha256  # 格式通常为 sha256=xxx
        self.size = size

    def __repr__(self):
        return f"<RecordEntry {self.path},{self.sha256},{self.size}>"


class RecordManager:
    """读写和操作 dist-info/RECORD 的类"""

    def __init__(self):
        self.entries: "dict[str, RecordEntry]" = {}

    def load_bytes(self, content):
        """
        从字节流加载 RECORD 内容

        Args:
            content (bytes):
        """
        entries = {}
        text_content = content.decode('utf-8')
        f = io.StringIO(text_content)

        # RECORD 是没有引号的 CSV
        reader = csv.reader(f, delimiter=',', quoting=csv.QUOTE_MINIMAL)
        for row in reader:
            if len(row) >= 3:
                path = to_posix(row[0])
                entries[path] = RecordEntry(path, row[1], row[2])
            elif len(row) > 0:  # 有些工具生成的 RECORD 可能只有路径（通常是 RECORD 自己）
                path = to_posix(row[0])
                entries[path] = RecordEntry(path, "", "")

        self.entries = entries

    def dump_bytes(self):
        """
        将当前记录序列化为 bytes

        Returns:
            bytes:
        """
        # 推荐排序以保证可复现性
        self.entries = {k: v for k, v in sorted(self.entries.items(), reverse=True)}

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

        # __pycache__/typing_extensions.cpython-38.pyc,,
        # typing_extensions-4.13.2.dist-info/INSTALLER,sha256=zuuue4knoyJ-UwPPXg8fezS7VCrXJQrAP7zeNuwvFQg,4
        # typing_extensions.py,sha256=o48qcATlT6qQcRLzOvazSjPXHU0nCbw5LShwl0jrtag,172654
        # flask/__init__.py,sha256=9ZCelLoNCpr6eSuLmYlzvbp12B3lrLgoN5U2UWk1vdo,2251
        # ../../Scripts/flask.exe,sha256=S0-6lL0qtZxuaPnj-z8g9RYkziez-D0R8TkGAlDMJjM,106346
        return f.getvalue().encode('utf-8')

    def add_content(self, path, data):
        """
        给定文件内容，计算并添加记录

        Args:
            path (str):
            data (bytes | None): None for no data, for file like RECORD itself or .pyc files
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
        给定磁盘路径，读取文件并添加记录

        Args:
            path (str):
            abspath (str):
        """
        data = atomic_read_bytes(abspath)
        self.add_content(path, data)

    def iter_py_files(self):
        """
        Yields:
            RecordEntry:
        """
        for path, entry in self.entries.items():
            if path.endswith('.py'):
                yield entry
