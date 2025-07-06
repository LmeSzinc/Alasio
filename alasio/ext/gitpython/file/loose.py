import os

from alasio.ext.path.atomic import atomic_read_bytes
from alasio.ext.gitpython.file.exception import ObjectBroken, PackBroken
from alasio.ext.gitpython.obj.obj import GitLooseObject, parse_loosedata


class LoosePath:
    def __init__(self, path):
        """
        Args:
            path (str): Absolute path to .git/objects
        """
        self.path: str = path

        # all git objects
        # key: sha1 of git object, value: GitObject
        self.dict_object: "dict[str, GitLooseObject]" = {}
        # always empty in LoosePath
        self.dict_object_data: "dict[str, memoryview]" = {}
        # git objects that not yet read
        # key: sha1 of git object, value: self
        self.dict_object_unread: "dict[str, LoosePath]" = {}

    def clear_object(self):
        self.dict_object = {}
        self.dict_object_data = {}
        self.dict_object_lazy = {}

    def _loose_iter(self):
        """
        Iter all loose objects

        Yields:
            Tuple[str, str]: sha1, filepath
        """
        # scan root folder
        try:
            list_entry = list(os.scandir(self.path))
        except (FileNotFoundError, NotADirectoryError):
            list_entry = []

        for entry in list_entry:
            header = entry.name
            if len(header) != 2:
                # "info" or "pack"
                continue
            # scan sub folder
            try:
                sub_entry = list(os.scandir(entry.path))
            except (FileNotFoundError, NotADirectoryError):
                # folder appears at root folder, but disappear in second scan
                continue
            for file_entry in sub_entry:
                name = file_entry.name
                if len(name) == 38:
                    # sha1 of length 40
                    sha1 = header + name
                    yield sha1, file_entry.path

    def loose_read_full(self):
        """
        Read all files in .git/objects
        """
        dict_object = {}

        for sha1, file in self._loose_iter():
            try:
                data = atomic_read_bytes(file)
            except FileNotFoundError:
                # file appears when scanning, but disappeared at when reading
                continue
            try:
                obj = parse_loosedata(data)
            except ObjectBroken:
                # Ignore broken objects
                continue
            dict_object[sha1] = obj

        self.dict_object = dict_object
        self.dict_object_data = {}
        self.dict_object_unread = {}

    def loose_read_lazy(self):
        """
        Scan all files in .git/objects, but not reading the file content
        """
        dict_object_unread = {}

        for sha1, _ in self._loose_iter():
            dict_object_unread[sha1] = self

        self.dict_object = {}
        self.dict_object_data = {}
        self.dict_object_unread = dict_object_unread

    def addread(self, sha1):
        """
        Args:
            sha1 (str): sha1 of length 40

        Returns:
            GitLooseObject:

        Raises:
            PackBroken:
            ObjectBroken:
        """
        file = f'{self.path}/{sha1[:2]}/{sha1[2:]}'
        # read
        try:
            data = atomic_read_bytes(file)
        except FileNotFoundError:
            raise PackBroken(f'Missing loose object sha1={sha1}')
        obj = parse_loosedata(data)

        # set attribute
        # self.dict_object[sha1] = obj
        # self.dict_object_unread.pop(sha1, None)
        return obj
