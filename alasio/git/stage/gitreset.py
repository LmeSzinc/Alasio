from collections import deque

import msgspec

from alasio.ext.concurrent.threadpool import THREAD_POOL
from alasio.ext.path.atomic import file_write
from alasio.git.file.gitobject import GitObjectManager
from alasio.git.stage.hashobj import git_file_hash


class FileEntry(msgspec.Struct):
    sha1: str
    mode: bytes
    path: str


class GitReset(GitObjectManager):
    def list_files(self, sha1):
        """
        List all files under the tree of given sha1

        Args:
            sha1 (str): commit sha1, or tree sha1, or tag sha1

        Returns:
            dict[str, FileEntry]:
        """
        queue = deque([sha1])

        # list of (parent_tree_sha1, EntryObject) for file entries
        # Using a list instead of dict to correctly handle multiple files with the same sha1
        # (e.g. multiple empty __init__.py files share the same sha1)
        list_file = []
        # key: tree sha1, value: parent tree sha1
        dict_parent: "dict[str, str]" = {}
        # key: tree sha1, value: directory name
        dict_path: "dict[str, str]" = {}

        while 1:
            new_queue = deque()
            # iter tree objects
            for sha in queue:
                obj = self.cat(sha)
                typ = obj.type
                # tree
                if typ == 2:
                    tree = obj.decoded
                    for entry in tree:
                        mode = entry.mode
                        # directory
                        if mode == b'40000':
                            dict_parent[entry.sha1] = sha
                            new_queue.append(entry.sha1)
                            dict_path[entry.sha1] = entry.name
                        # submodule
                        elif mode == b'160000':
                            dict_parent[entry.sha1] = sha
                            new_queue.append(entry.sha1)
                            dict_path[entry.sha1] = entry.name
                        # file
                        else:
                            # Record (parent_tree_sha1, entry) so each file is unique by position
                            list_file.append((sha, entry))
                    continue
                # commit
                if typ == 1:
                    commit = obj.decoded
                    new_queue.append(commit.tree)
                    continue
                # tag
                if typ == 4:
                    tag = obj.decoded
                    new_queue.append(tag.object)
                    continue
                # file
                if typ == 3:
                    raise ValueError('Object is a file, cannot iter files in it')
            # End, no more tree to iter
            queue = new_queue
            if not queue:
                break

        # build filepath
        # key: file path, value: FileEntry
        dict_entry = {}
        for parent_sha, entry in list_file:
            paths = deque([entry.name])
            tree_sha = parent_sha
            while True:
                name = dict_path.get(tree_sha)
                if name:
                    paths.appendleft(name)
                tree_sha = dict_parent.get(tree_sha)
                if tree_sha is None:
                    break
            file_path = '/'.join(paths)
            file = FileEntry(sha1=entry.sha1, mode=entry.mode, path=file_path)
            dict_entry[file_path] = file

        return dict_entry

    @staticmethod
    def _reset_task_iter(dict_file):
        """
        Split dict_file by every 50 files.
        50 is the magic number, ALAS has average file size 23.6KB and SRC is 22.8KB,
        so 50 files are about 1MB for each task to read.

        Args:
            dict_file (dict[str, FileEntry]): files that need validate

        Yields:
            ict[str, FileEntry]:
        """
        count = 0
        dict_task = {}
        for sha1, file in dict_file.items():
            dict_task[sha1] = file
            count += 1
            if count >= 50:
                yield dict_task
                dict_task = {}
                count = 1

        yield dict_task

    def _reset_task_validate_files(self, dict_file):
        """
        Args:
            dict_file (dict[str, FileEntry]): files that need validate

        Returns:
            dict[str, FileEntry]: files that reset
        """
        root = self.path
        # validate files
        need_reset = {}
        for sha1, file in dict_file.items():
            filepath = f'{root}/{file.path}'
            try:
                sha1 = git_file_hash(filepath)
            except FileNotFoundError:
                # need to write new file
                need_reset[sha1] = file
                continue
            if file.sha1 != sha1:
                # need to reset file
                need_reset[sha1] = file

        # write files
        for sha1, file in need_reset.items():
            filepath = f'{root}/{file.path}'
            obj = self.cat(sha1)
            if obj.type != 3:
                # This shouldn't happen
                continue
            # write, no need to be atomic
            data = obj.decoded
            file_write(filepath, data)

        return need_reset

    def reset_validate_files(self, dict_file):
        """
        Validate local files by given `dict_file`

        Args:
            dict_file (dict[str, FileEntry]): files that need validate
        """
        tasks = list(self._reset_task_iter(dict_file))
        if not tasks:
            return
        if len(tasks) == 1:
            self._reset_task_validate_files(tasks[0])
        else:
            with THREAD_POOL.wait_jobs() as pool:
                for task in tasks:
                    pool.start_thread_soon(self._reset_task_validate_files, task)

    def git_reset_hard(self, sha1):
        """
        Equivalent to `git reset --hard {sha1}`

        Args:
            sha1 (str): commit sha1, or tree sha1, or tag sha1
        """
        self.read_lazy()
        dict_file = self.list_files(sha1)
        self.reset_validate_files(dict_file)
