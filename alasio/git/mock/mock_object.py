import msgspec

from alasio.ext.cache import cached_property
from alasio.git.attr.attr import GitAttributes
from alasio.git.mock.mock_base import MockGitRepoBase
from alasio.git.obj.obj import GitLooseObject
from alasio.git.stage.gitreset import FileEntry
from alasio.git.stage.hashobj import blob_hash


class MockBlobEntry(msgspec.Struct):
    """
    In-memory storage for a mocked git blob object.

    Attributes:
        content (bytes): Raw file content.
        mode (bytes): File mode in git format, e.g. b'100644'.
        blob_sha1 (str): Git blob sha1 hex digest.
    """
    content: bytes
    mode: bytes
    blob_sha1: str


class MockGitObject(MockGitRepoBase):
    """
    In-memory mock of GitRepo for testing.

    Provides the same list_files(), get_file(), cat(), read_full(),
    read_lazy(), cat_shallow() interface as GitObjectManager, without
    requiring a real git repository on disk.
    """

    def __init__(self, path=''):
        super().__init__(path)
        # commit_sha1 -> {path: MockBlobEntry}
        self._files: "dict[str, dict[str, MockBlobEntry]]" = {}
        # blob_sha1 -> MockBlobEntry
        self._objects: "dict[str, MockBlobEntry]" = {}
        # commit_sha1 -> commit object content in bytes
        self._commits: "dict[str, bytes]" = {}
        # current head commit sha1
        self._head: str = ''

    @cached_property
    def _gitattr(self):
        return GitAttributes()

    def register_file(self, sha1, path, content, mode=644):
        """
        Register a file under a commit sha1.

        The sha1 of the file content (blob sha1) is computed from the content automatically.

        To replicate git's EOL handling, MockGitObject always store LF in objects
        using the default rules in GitAttributes().
        Note that if registering a .gitattributes, its rules will be ignored,
        MockGitObject always uses the default rules, which is enough for testing.

        Args:
            sha1 (str): Commit sha1 that identifies the tree
            path (str): File path relative to repo root, e.g. 'src/main.py'
            content (bytes): File content
            mode (int): File mode. Only 644 and 755 are accepted.
                Defaults to 644.
        """
        if mode == 644:
            mode = b'100644'
        elif mode == 755:
            mode = b'100755'
        else:
            raise ValueError(f'Unsupported file mode: {mode}. Only 644 and 755 are accepted.')

        # Replicate git's EOL handling: always store LF in objects
        content = self._normalize_eol(path, content)

        blob_sha1 = blob_hash(content)

        if sha1 not in self._files:
            self._files[sha1] = {}
        entry = MockBlobEntry(content=content, mode=mode, blob_sha1=blob_sha1)
        self._files[sha1][path] = entry
        self._objects[blob_sha1] = entry

    def register_commit(self, sha1, parents=None, tree='tree',
                        author_name='', author_email='', author_time=0, author_tz=0,
                        committer_name=None, committer_email=None,
                        committer_time=None, committer_tz=None,
                        message=''):
        """
        Register a commit object under a commit sha1.

        The sha1 is an identifier like in register_file(), it does not
        have to be a real git sha1, e.g. 'c1'.

        The committer attributes default to the author attributes.

        Args:
            sha1 (str): Commit sha1 that identifies the commit
            parents (list[str], optional): Parent commit sha1s, empty for
                the initial commit
            tree (str): Tree sha1 that identifies the tree
            author_name (str): Author name
            author_email (str): Author email
            author_time (int): Author time, unix timestamp in seconds
            author_tz (int): Author timezone offset in minutes
            committer_name (str, optional): Committer name,
                defaults to the author name
            committer_email (str, optional): Committer email,
                defaults to the author email
            committer_time (int, optional): Committer time,
                defaults to the author time
            committer_tz (int, optional): Committer timezone offset,
                defaults to the author timezone
            message (str): Commit message
        """
        if committer_name is None:
            committer_name = author_name
        if committer_email is None:
            committer_email = author_email
        if committer_time is None:
            committer_time = author_time
        if committer_tz is None:
            committer_tz = author_tz

        rows = [f'tree {tree}']
        for parent in parents or []:
            rows.append(f'parent {parent}')
        rows.append(
            f'author {author_name} <{author_email}> {author_time} '
            f'{self._format_tz(author_tz)}'
        )
        rows.append(
            f'committer {committer_name} <{committer_email}> {committer_time} '
            f'{self._format_tz(committer_tz)}'
        )
        content = '\n'.join(rows).encode() + b'\n\n' + message.encode()
        self._commits[sha1] = content

    @staticmethod
    def _format_tz(tz):
        """
        Format timezone offset in minutes to git format like "+0800".

        Args:
            tz (int): Timezone offset in minutes

        Returns:
            str: Timezone in git format
        """
        sign = '+' if tz >= 0 else '-'
        tz = abs(tz)
        return f'{sign}{tz // 60:02d}{tz % 60:02d}'

    def register_head(self, sha1):
        """
        Set the repo head.

        Args:
            sha1 (str): Commit sha1
        """
        self._head = sha1

    def head_get(self, head=None):
        """
        Get current git HEAD.

        Args:
            head: Ignored, the mock has a single head

        Returns:
            str: sha1, or empty string ""
        """
        return self._head

    def _normalize_eol(self, path, content):
        """
        Normalize line endings of file content for storage in git objects.

        Text files are stored with LF only, binary files are stored as-is.
        The text/binary decision follows the default rules in GitAttributes().

        Args:
            path (str): File path relative to repo root
            content (bytes): File content

        Returns:
            bytes: Content with CRLF converted to LF for text files,
                unchanged for binary files
        """
        attrs_dict = self._gitattr.apply_files([path])[0].attrs_dict
        mode = attrs_dict.get('text', 'auto')
        if mode == 'set':
            is_text = True
        elif mode == 'unset':
            is_text = False
        else:
            # text="auto", decide by content
            is_text = b'\x00' not in content
        if is_text:
            return content.replace(b'\r\n', b'\n')
        return content

    def compare_commit(self, old, new):
        """
        Compare two commits and return added, modified, and deleted files.

        Compares the flat file lists registered for each commit sha1.
        A file is considered modified when it exists in both commits but
        has a different sha1 or mode.  Renamed files are detected as a
        deletion at the old path plus an addition at the new path.

        Args:
            old (str): Commit sha1 for the old state.
            new (str): Commit sha1 for the new state.

        Returns:
            tuple[dict[str, FileEntry], dict[str, FileEntry], dict[str, FileEntry]]:
                (added, modified, deleted).
        """
        old_files = self._files.get(old, {})
        new_files = self._files.get(new, {})

        added = {}
        modified = {}
        deleted = {}

        old_paths = set(old_files)
        new_paths = set(new_files)

        # Files only in new → added
        for path in new_paths - old_paths:
            entry = new_files[path]
            added[path] = FileEntry(sha1=entry.blob_sha1, mode=entry.mode, path=path)

        # Files only in old → deleted
        for path in old_paths - new_paths:
            entry = old_files[path]
            deleted[path] = FileEntry(sha1=entry.blob_sha1, mode=entry.mode, path=path)

        # Files in both → compare sha1 and mode
        for path in old_paths & new_paths:
            old_entry = old_files[path]
            new_entry = new_files[path]
            if old_entry.blob_sha1 != new_entry.blob_sha1 or old_entry.mode != new_entry.mode:
                modified[path] = FileEntry(sha1=new_entry.blob_sha1, mode=new_entry.mode, path=path)

        return added, modified, deleted

    # ── GitObjectManager interface ────────────────────────────────────────

    def read_full(self):
        """
        Read all objects from the repository.

        Mock: no-op since all data is already in memory.
        Returns self.
        """
        return self

    def read_lazy(self, skip_size=None):
        """
        Read objects lazily, skipping large objects.

        Mock: no-op since all data is already in memory.
        Returns self.
        """
        return self

    def cat_shallow(self, sha1):
        """
        Get object from given sha1.

        Args:
            sha1 (str): Commit or blob sha1

        Returns:
            GitLooseObject: Commit or blob object

        Raises:
            KeyError: If sha1 is not found in any registered object
        """
        # commit
        try:
            content = self._commits[sha1]
        except KeyError:
            pass
        else:
            return GitLooseObject(type=1, size=len(content), data=content)

        # blob
        blob_entry = self._objects.get(sha1)
        if blob_entry is not None:
            return GitLooseObject(type=3, size=len(blob_entry.content), data=blob_entry.content)

        raise KeyError(f'No such object sha1={sha1}')

    def cat(self, sha1):
        """
        Get object from given sha1.

        Delegates to cat_shallow — there are no delta objects in mock data.

        Args:
            sha1 (str): Blob sha1

        Returns:
            GitLooseObject: Blob object

        Raises:
            KeyError: If sha1 is not found in any registered file
        """
        return self.cat_shallow(sha1)

    # ── Mock-specific helpers ─────────────────────────────────────────────

    def list_files(self, sha1):
        """
        List all files under a given commit sha1.

        Args:
            sha1 (str): Commit sha1

        Returns:
            dict[str, FileEntry]: filepath -> FileEntry
        """
        files = self._files.get(sha1)
        if files is None:
            return {}

        result = {}
        for path, entry in files.items():
            result[path] = FileEntry(sha1=entry.blob_sha1, mode=entry.mode, path=path)
        return result

    def list_commit_have(self, sha1, have_lookback=20):
        """
        List commits before given sha1 (include given sha1) on the same branch.

        Merge commits follow the first parent.

        Args:
            sha1 (str): Commit sha1
            have_lookback (int): Maximum lookback, 0 to return all

        Returns:
            dict[str, CommitObj]: Key: sha1 in str, value: CommitObj

        Raises:
            KeyError: If sha1 is not a registered commit
        """
        out = {}
        count = 0
        while True:
            obj = self.cat(sha1)
            commit = obj.decoded
            out[sha1] = commit
            count += 1

            parent = commit.parent
            parent_type = type(parent)
            if parent_type is str:
                sha1 = parent
            elif parent_type is list:
                # merge commit, pick the first parent
                sha1 = parent[0]
            elif not parent:
                # initial commit, no parent
                break

            # check if reached limit
            if have_lookback:
                if count >= have_lookback:
                    break

        return out

    def get_file(self, sha1, filepath):
        """
        Get a single FileEntry by filepath from a given commit sha1.

        Args:
            sha1 (str): Commit sha1
            filepath (str): File path relative to repo root

        Returns:
            FileEntry | None: FileEntry if found, None otherwise
        """
        files = self._files.get(sha1)
        if files is None:
            return None

        blob_entry = files.get(filepath)
        if blob_entry is None:
            return None

        return FileEntry(sha1=blob_entry.blob_sha1, mode=blob_entry.mode, path=filepath)
