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
            sha1 (str): Blob sha1

        Returns:
            GitLooseObject: Blob object

        Raises:
            KeyError: If sha1 is not found in any registered file
        """
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
