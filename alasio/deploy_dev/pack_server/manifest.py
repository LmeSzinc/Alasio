"""
The manifest of a workspace: what a run publishes and what it keeps private.

The pack server generates one workspace per repo and mirrors it to one or more
static hosts (see publish). The mirror is the workspace as it is, so this file
is the one place that says which entries of that tree are served to the
clients:

    {workspace}/manifest.json

- files: the entries of the run that are published, relative to the workspace;
- private: the entries that stay on the disk of the generator, e.g. the wheel
  cache in .wheel_cache/.

The split is decided by the generator, once: a path with a segment that starts
with '.' or '_' is private, everything else is published. The uploader and the
cleaner read the lists, they do not classify a path of their own, so the rule
lives in one place only.

The manifest is also the record of what the cleanup of the run keeps: the
workspace is cleaned by RepoBase.cleanup() against the same set of touched
files the two halves come from, so a cache entry survives the runs that still
fetch it and goes away with the run that stops. The file is written with the
publication barrier, see repo.Repo.commit(): it names the version latest.pack
serves, so a manifest and a barrier of two different runs are never paired.

generated_at is the time the manifest was first written for its version, not
the time of the last write: a run that changes nothing writes nothing, and the
field keeps meaning "when this generation started". It is an audit value, no
decision is taken from it.

Usage:
    from alasio.deploy_dev.pack_server.manifest import Manifest

    files, private = base.repo_manifest()
    manifest = Manifest.build(
        channel='Author_Repo_master', version='3f2b9c1d', checksum='0a1b...',
        files=files, private=private,
    )
    manifest.write(file)
"""

import time
from typing import List

import msgspec

from alasio.ext.file.jsonfile import json_dumps
from alasio.ext.path.atomic import atomic_read_bytes, atomic_write, is_tmp_file

# the name of the manifest in the workspace, and the object key of it on a
# mirror: {Prefix}/{workspace}/manifest.json
MANIFEST_FILE = 'manifest.json'

# the format version of the manifest, bumped when a reader of an older schema
# cannot take the file (a new field with a default does not need one)
MANIFEST_SCHEMA = 1

# the first character of a path segment that marks the path as private: the
# dot is the convention of a hidden folder on linux (shell glob, rsync, the
# default view of a file manager), the underscore is the one this project used
# before the cache was renamed, a leftover of it is private too
PRIVATE_PREFIXES = ('.', '_')


def is_private_path(path):
    """
    Whether a workspace-relative path is kept off the mirror.

    Only the segments below the workspace are looked at: the workspace name
    itself is a single path component and never starts with '.' or '_', see
    RepoBase.workspace.

    Args:
        path (str): '/'-separated path relative to the workspace

    Returns:
        bool: True when the path is private
    """
    return any(part.startswith(PRIVATE_PREFIXES) for part in path.split('/'))


def is_temp_path(path):
    """
    Whether a path is the tmp file of an interrupted write.

    Args:
        path (str): '/'-separated path relative to the workspace

    Returns:
        bool: True when the file name is a tmp name, e.g. x.yaml.sTD2kF.tmp
    """
    return is_tmp_file(path.rpartition('/')[2])


def split_paths(paths):
    """
    Split the paths of a run into the published ones and the private ones.

    The manifest itself and the tmp files of an interrupted write are entries
    of neither list: the first is the file the lists are written into, the
    second is not a file of the run at all (the atomic writes never leave one
    at a final path).

    Args:
        paths (Iterable[str]): '/'-separated paths relative to the workspace

    Returns:
        tuple[list[str], list[str]]: (files, private), both sorted so the
            manifest of an unchanged run is the same bytes
    """
    files = []
    private = []
    for path in paths:
        if path == MANIFEST_FILE or is_temp_path(path):
            continue
        target = private if is_private_path(path) else files
        target.append(path)
    return sorted(files), sorted(private)


class Manifest(msgspec.Struct):
    """
    The manifest of one generation of a workspace, see the module docstring.

    Attributes:
        schema (int): Format version of the file, MANIFEST_SCHEMA
        channel (str): Name of the workspace, the first segment of the object
            key of every file of it
        version (str): Version latest.pack serves, the barrier of the run
        checksum (str): Index pack checksum latest.pack carries, hex string
        generated_at (int): Unix time the manifest of this version was first
            written, kept across rewrites of the same generation
        files (List[str]): Published files, '/'-separated, relative to the
            workspace, sorted
        private (List[str]): Files that stay on the disk of the generator,
            same form
    """

    schema: int
    channel: str
    version: str
    checksum: str
    generated_at: int
    files: List[str]
    private: List[str] = msgspec.field(default_factory=list)

    @classmethod
    def build(cls, channel, version, checksum, files, private, generated_at=None):
        """
        Build the manifest of a run.

        Args:
            channel (str): Name of the workspace
            version (str): Version latest.pack serves
            checksum (str): Index pack checksum of that version
            files (Iterable[str]): Published files, see split_paths
            private (Iterable[str]): Private files, see split_paths
            generated_at (int, optional): Time the generation started, the
                current time when None. Defaults to None

        Returns:
            Manifest: The manifest
        """
        return cls(
            schema=MANIFEST_SCHEMA,
            channel=channel,
            version=version,
            checksum=checksum,
            generated_at=int(time.time()) if generated_at is None else generated_at,
            files=sorted(files),
            private=sorted(private),
        )

    @classmethod
    def decode(cls, data):
        """
        Decode the content of a manifest file.

        Args:
            data (bytes): Content of the file

        Returns:
            Manifest: The decoded manifest

        Raises:
            msgspec.DecodeError: If the content is not a manifest of this
                schema: a broken file, a JSON document of another shape, or a
                file of another format version
        """
        manifest = msgspec.json.decode(data, type=cls)
        if manifest.schema != MANIFEST_SCHEMA:
            raise msgspec.ValidationError(
                f'Manifest schema {manifest.schema}, expected {MANIFEST_SCHEMA}')
        return manifest

    @classmethod
    def from_file(cls, file):
        """
        Read the manifest of a workspace.

        Args:
            file (str): Path of the manifest

        Returns:
            Manifest: The manifest of the file

        Raises:
            FileNotFoundError: If the workspace has no manifest yet
            msgspec.DecodeError: If the file is not a manifest, see decode
        """
        return cls.decode(atomic_read_bytes(file))

    def dump(self):
        """
        Encode the manifest as the content of its file.

        The fields are written in the order of the struct and the two lists are
        sorted by build(), so the bytes of an unchanged generation are the same
        bytes: a run that changes nothing does not rewrite the file, and an
        uploader that compares content does not upload it again.

        Returns:
            bytes: Content of the manifest file
        """
        # msgspec has no indented encoder, the file is small and read by a
        # person as often as by a program: the json of the project writes it
        return json_dumps(msgspec.to_builtins(self))

    def write(self, file):
        """
        Write the manifest to a file, unless the file already holds it.

        The generated_at of the file is kept when the version and the checksum
        are the ones of this manifest: the generation is the same, and its
        start time does not move because the run of today read the same files.
        A file that is missing, broken or of another generation is replaced.

        Args:
            file (str): Path of the manifest, created with its folder

        Returns:
            bool: True when the file was written, False when it already held
                this manifest
        """
        try:
            current = atomic_read_bytes(file)
        except FileNotFoundError:
            current = None
        if current is not None:
            try:
                existing = self.decode(current)
            except msgspec.DecodeError:
                # a broken file is not a base, the run writes its own
                existing = None
            if existing is not None and (existing.version, existing.checksum) == (
                    self.version, self.checksum):
                self.generated_at = existing.generated_at
        data = self.dump()
        if current == data:
            return False
        atomic_write(file, data)
        return True
