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
        # all collected submodule sha1, we can't look into submodules
        set_submodule_sha1 = set()

        while 1:
            new_queue = deque()
            # iter tree objects
            for sha in queue:
                if sha in set_submodule_sha1:
                    continue
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
                            set_submodule_sha1.add(entry.sha1)
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

    def get_file(self, sha1, filepath):
        """
        Get a single FileEntry by filepath from the given commit/tree/tag sha1.
        Only traverses the specific path, avoiding full tree enumeration.

        Args:
            sha1 (str): commit sha1, or tree sha1, or tag sha1
            filepath (str): file path relative to repo root, e.g. 'alasio/git/stage/gitreset.py'

        Returns:
            FileEntry | None: FileEntry if found, None otherwise
        """
        # Resolve commit/tag to tree sha1
        while 1:
            obj = self.cat(sha1)
            typ = obj.type
            if typ == 1:
                # commit -> tree
                sha1 = obj.decoded.tree
            elif typ == 4:
                # tag -> object
                sha1 = obj.decoded.object
            else:
                break

        # Now sha1 should be a tree sha1 (type 2)
        if typ == 3:
            raise ValueError('Object is a file, cannot get file in it')
        if typ != 2:
            return None

        parts = filepath.split('/')
        if not parts:
            return None

        # Walk down the tree hierarchy along the specific path
        for idx, name in enumerate(parts):
            obj = self.cat(sha1)
            if obj.type != 2:
                return None
            tree = obj.decoded

            # Find the entry matching the current path component
            found = None
            for entry in tree:
                if entry.name == name:
                    found = entry
                    break

            if found is None:
                return None

            is_last = idx == len(parts) - 1

            if is_last:
                # We reached the target file
                if found.mode in (b'40000', b'160000'):
                    # path points to a directory or submodule, not a file
                    return None
                return FileEntry(sha1=found.sha1, mode=found.mode, path=filepath)

            # Not the last component, must be a directory to continue
            if found.mode != b'40000':
                # Not a directory, can't descend
                return None
            sha1 = found.sha1

        # Should not reach here
        return None

    def compare_commit(self, old, new):
        """
        Compare two commits and return added, modified, and deleted files.

        Walks the commit trees side-by-side to find differences.
        A file is considered modified when it exists in both commits but
        has a different sha1 or mode.  Renamed files are detected as a
        deletion at the old path plus an addition at the new path.

        Args:
            old (str): Old commit sha1 (or tree / tag sha1).
            new (str): New commit sha1 (or tree / tag sha1).

        Returns:
            tuple[dict[str, FileEntry], dict[str, FileEntry], dict[str, FileEntry]]:
                (added, modified, deleted).
        """
        # Resolve commit/tag to tree sha1
        def _resolve_tree(sha1):
            while True:
                obj = self.cat(sha1)
                if obj.type == 1:  # commit
                    sha1 = obj.decoded.tree
                elif obj.type == 4:  # tag
                    sha1 = obj.decoded.object
                else:
                    return sha1

        old_tree = _resolve_tree(old)
        new_tree = _resolve_tree(new)
        added, modified, deleted = self._compare_trees(old_tree, new_tree, '')
        return added, modified, deleted

    def _compare_trees(self, old_tree_sha1, new_tree_sha1, prefix):
        """
        Recursively compare two git tree objects.

        Args:
            old_tree_sha1 (str | None): Old tree sha1, or None when the
                entire tree is absent (all entries are additions).
            new_tree_sha1 (str | None): New tree sha1, or None when the
                entire tree is absent (all entries are deletions).
            prefix (str): Path prefix accumulated from parent trees.

        Returns:
            tuple[dict[str, FileEntry], dict[str, FileEntry], dict[str, FileEntry]]:
                (added, modified, deleted).
        """
        added = {}
        modified = {}
        deleted = {}

        old_entries = {}
        new_entries = {}
        all_names = set()

        if old_tree_sha1 is not None:
            for entry in self.cat(old_tree_sha1).decoded:
                old_entries[entry.name] = entry
                all_names.add(entry.name)

        if new_tree_sha1 is not None:
            for entry in self.cat(new_tree_sha1).decoded:
                new_entries[entry.name] = entry
                all_names.add(entry.name)

        for name in all_names:
            path = f'{prefix}/{name}' if prefix else name
            old_entry = old_entries.get(name)
            new_entry = new_entries.get(name)

            if old_entry is None:
                # Entry exists only in the new tree → added
                if new_entry.mode == b'40000':
                    sub_added, _, _ = self._compare_trees(new_entry.sha1, None, path)
                    added.update(sub_added)
                else:
                    added[path] = FileEntry(sha1=new_entry.sha1, mode=new_entry.mode, path=path)

            elif new_entry is None:
                # Entry exists only in the old tree → deleted
                if old_entry.mode == b'40000':
                    _, _, sub_deleted = self._compare_trees(old_entry.sha1, None, path)
                    deleted.update(sub_deleted)
                else:
                    deleted[path] = FileEntry(sha1=old_entry.sha1, mode=old_entry.mode, path=path)

            elif old_entry.mode == b'40000' and new_entry.mode == b'40000':
                # Both are directories
                if old_entry.sha1 == new_entry.sha1:
                    # Entire subtree unchanged
                    continue
                sub_added, sub_modified, sub_deleted = self._compare_trees(
                    old_entry.sha1, new_entry.sha1, path,
                )
                added.update(sub_added)
                modified.update(sub_modified)
                deleted.update(sub_deleted)

            elif old_entry.mode == b'40000' and new_entry.mode != b'40000':
                # Directory became a file / submodule / symlink:
                # all old directory contents are deleted, the new entry is added.
                _, _, sub_deleted = self._compare_trees(old_entry.sha1, None, path)
                deleted.update(sub_deleted)
                added[path] = FileEntry(sha1=new_entry.sha1, mode=new_entry.mode, path=path)

            elif old_entry.mode != b'40000' and new_entry.mode == b'40000':
                # File / submodule / symlink became a directory:
                # the old entry is deleted, all new directory contents are added.
                deleted[path] = FileEntry(sha1=old_entry.sha1, mode=old_entry.mode, path=path)
                sub_added, _, _ = self._compare_trees(None, new_entry.sha1, path)
                added.update(sub_added)

            elif old_entry.sha1 != new_entry.sha1 or old_entry.mode != new_entry.mode:
                # Both are non-directories but differ in content or mode → modified
                modified[path] = FileEntry(sha1=new_entry.sha1, mode=new_entry.mode, path=path)

            # else: both are non-directories and identical → skip

        return added, modified, deleted

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
