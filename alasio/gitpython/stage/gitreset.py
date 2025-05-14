from collections import deque

import msgspec

from alasio.gitpython.file.gitobject import GitObjectManager


class FileEntry(msgspec.Struct):
    sha1: str
    mode: bytes
    path: str


class GitReset(GitObjectManager):
    def list_files(self, sha1):
        """
        List all files under the tree of given sha1

        Args:
            sha1: commit sha1, tree sha1, tag sha1

        Returns:
            list[FileEntry]:
        """
        queue = deque([sha1])

        # key: file sha1, value: GitObject
        dict_file = {}
        # key: object sha1, value: parent sha1
        dict_parent: "dict[str, str]" = {}
        # key: directory sha1 or submodule sha1, value: str
        dict_path = {}

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
                        dict_parent[entry.sha1] = sha
                        mode = entry.mode
                        # directory
                        if mode == b'40000':
                            new_queue.append(entry.sha1)
                            dict_path[entry.sha1] = entry.name
                        # submodule
                        elif mode == b'160000':
                            new_queue.append(entry.sha1)
                            dict_path[entry.sha1] = entry.name
                        # file
                        else:
                            dict_file[entry.sha1] = entry
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
        output = []
        append = output.append
        for sha1, entry in dict_file.items():
            paths = deque([entry.name])
            parent_sha1 = sha1
            while 1:
                parent_sha1 = dict_parent.get(parent_sha1, None)
                if parent_sha1:
                    path = dict_path.get(parent_sha1, None)
                    if path:
                        paths.appendleft(path)
                        continue
                break
            paths = '/'.join(paths)
            file = FileEntry(sha1=sha1, mode=entry.mode, path=paths)
            append(file)

        return output
