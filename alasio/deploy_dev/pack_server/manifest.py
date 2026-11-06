"""
The manifest of a workspace: what a run publishes and what it keeps private.

The pack server generates one workspace per repo and mirrors it to one or more
static hosts (see publish). The mirror is the workspace as it is, so the
manifest is the one place that says which entries of that tree are served to
the clients. The record is two files, each one written for its reader:

    {workspace}/manifest.json    the published contract: Manifest
    {workspace}/.manifest.json   the local record: LocalManifest

- files: the entries of the run that are published, relative to the workspace.
  A mirror serves exactly these entries, and the uploader reads no other list;
- private: the entries that stay on the disk of the generator, e.g. the wheel
  cache in .wheel_cache/. They are never uploaded, so a reader of a mirror has
  no use of them.

Two files rather than two lists of one file: the uploader mirrors the published
file as it is, without reading a field, filtering an entry or encoding a second
copy of the bytes, so the object of a mirror is byte for byte the file of the
disk. A list only the local side needs stays on the local side.

The split is decided by the generator, once: a path with a segment that starts
with '.' or '_' is private, everything else is published. The uploader and the
cleaner read the list of their own file, they do not classify a path of their
own, so the rule lives in one place only.

The local record is also the record of what the cleanup of the run keeps: the
workspace is cleaned by RepoBase.cleanup() against the same set of touched
files the two lists come from, so a cache entry survives the runs that still
fetch it and goes away with the run that stops. Both files are written with the
publication barrier, see repo.Repo.commit(): they name the version latest.pack
serves, so a manifest and a barrier of two different runs are never paired.

generated_at is the time the published contract was first written for its
version, not the time of the last write: a run that changes nothing writes
nothing, and the field keeps meaning "when this generation started". It is an
audit value, no decision is taken from it.

Usage:
    from alasio.deploy_dev.pack_server.manifest import LocalManifest, Manifest

    files, private = base.repo_manifest()
    Manifest.build(
        channel='Author_Repo_master', version='3f2b9c1d', checksum='0a1b...',
        files=files,
    ).write('{workspace}/manifest.json')
    LocalManifest.build(
        channel='Author_Repo_master', version='3f2b9c1d', checksum='0a1b...',
        private=private,
    ).write('{workspace}/.manifest.json')
"""

import time
from typing import List

import msgspec

from alasio.ext.file.jsonfile import json_dumps
from alasio.ext.path.atomic import atomic_read_bytes, atomic_write, is_tmp_file

# the file of the published contract in the workspace, and the object key of it
# on a mirror: {Prefix}/{workspace}/manifest.json
MANIFEST_FILE = 'manifest.json'

# the file of the local record in the workspace. The dot marks a private path
# by the same rule as the cache folder: the workspace mirrors to the server as
# it is, and this file is not one a mirror serves
LOCAL_MANIFEST_FILE = '.manifest.json'

# the files the record of a generation is written in. Neither is an entry of
# its lists: the published one is the contract itself, which the uploader puts
# last on its own, and the local one never leaves the disk
MANIFEST_FILES = (MANIFEST_FILE, LOCAL_MANIFEST_FILE)

# the format version of the published contract, bumped when a reader of an
# older schema cannot take the file (a new field with a default does not need
# one)
MANIFEST_SCHEMA = 1

# the format version of the local record, independent from the one above: the
# file is read by the generator and the operator of the run only, so its format
# may change without touching the contract of a mirror
LOCAL_MANIFEST_SCHEMA = 1

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

    The two files the record is written in and the tmp files of an interrupted
    write are entries of neither list: the first are the record itself, the
    second are not files of the run at all (the atomic writes never leave one
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
        if path in MANIFEST_FILES or is_temp_path(path):
            continue
        target = private if is_private_path(path) else files
        target.append(path)
    return sorted(files), sorted(private)


def _decode(data, cls, schema):
    """
    Decode a manifest file of one of the two formats.

    Args:
        data (bytes): Content of the file
        cls (type): Manifest or LocalManifest
        schema (int): Format version the class reads

    Returns:
        ManifestBase: The decoded manifest

    Raises:
        msgspec.DecodeError: If the content is not a manifest of this schema: a
            broken file, a JSON document of another shape, or a file of another
            format version
    """
    manifest = msgspec.json.decode(data, type=cls)
    if manifest.schema != schema:
        raise msgspec.ValidationError(
            f'{cls.__name__} schema {manifest.schema}, expected {schema}')
    return manifest


class ManifestBase(msgspec.Struct):
    """
    The identity a generation has in both files of its record.

    The published contract and the local record are paired by these fields: a
    reader that holds both checks they name the same generation, and a pair of
    two runs is never taken for one.

    The file is read and written here, once, for both formats: from_file(),
    dump() and write() are the same file I/O whatever the class holds. A
    subclass provides the fields of its format (build()) and decode(), which
    checks the format version of the file, and overrides keep_from() when its
    file carries a value that outlives a rewrite, see Manifest.

    Attributes:
        schema (int): Format version of the file, MANIFEST_SCHEMA or
            LOCAL_MANIFEST_SCHEMA
        channel (str): Name of the workspace, the first segment of the object
            key of every file of it
        version (str): Version latest.pack serves, the barrier of the run
        checksum (str): Index pack checksum latest.pack carries, hex string
    """

    schema: int
    channel: str
    version: str
    checksum: str

    def keep_from(self, current):
        """
        Keep the values of the file on the disk that this manifest does not set.

        write() calls this before it compares the content: a file that names the
        same generation is the base of this write, so a value the run does not
        decide itself stays as the file holds it. The local record has no such
        value, the published contract keeps its generated_at.

        Args:
            current (bytes): Content of the file, None when it is missing
        """

    def dump(self):
        """
        Encode the manifest as the content of its file.

        The fields are written in the order of the struct and the lists are
        sorted by build(), so the bytes of an unchanged generation are the same
        bytes: a run that changes nothing does not rewrite the file, and an
        uploader that compares content does not upload it again.

        Returns:
            bytes: Content of the manifest file
        """
        # msgspec has no indented encoder, the file is small and read by a
        # person as often as by a program: the json of the project writes it
        return json_dumps(msgspec.to_builtins(self))

    @classmethod
    def from_file(cls, file):
        """
        Read a manifest of this format from a file.

        Args:
            file (str): Path of the manifest

        Returns:
            ManifestBase: The manifest of the file, an instance of the class
                this is called on

        Raises:
            FileNotFoundError: If the file is not there yet
            msgspec.DecodeError: If the file is not a manifest of this format,
                see decode
        """
        return cls.decode(atomic_read_bytes(file))

    def write(self, file):
        """
        Write the manifest to a file, unless the file already holds it.

        A file of another generation, or one that is broken, is replaced. A file
        that already holds this manifest is left as it is, so a run that changes
        nothing does not touch it and an uploader that compares content does not
        upload it again.

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
        self.keep_from(current)
        data = self.dump()
        if current == data:
            return False
        atomic_write(file, data)
        return True


class Manifest(ManifestBase):
    """
    The published contract of one generation: the files a mirror serves.

    It is the file an uploader mirrors as it is and the file a cleaner reads to
    know what to keep, so it names the published half only. The files that stay
    on the disk of the generator are in LocalManifest, the record of the same
    generation next to this one.

    Attributes:
        generated_at (int): Unix time the manifest of this version was first
            written, kept across rewrites of the same generation
        files (List[str]): Published files, '/'-separated, relative to the
            workspace, sorted
    """

    generated_at: int
    files: List[str]

    @classmethod
    def build(cls, channel, version, checksum, files, generated_at=None):
        """
        Build the published contract of a run.

        Args:
            channel (str): Name of the workspace
            version (str): Version latest.pack serves
            checksum (str): Index pack checksum of that version
            files (Iterable[str]): Published files, see split_paths
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
        )

    @classmethod
    def decode(cls, data):
        """
        Decode the content of a published manifest file.

        Args:
            data (bytes): Content of the file

        Returns:
            Manifest: The decoded manifest

        Raises:
            msgspec.DecodeError: If the content is not a published manifest of
                this schema, see _decode
        """
        return _decode(data, cls, MANIFEST_SCHEMA)

    def keep_from(self, current):
        """
        Keep the generated_at of the generation the file already holds.

        The file is the base of this write only when it names the same version
        and checksum: the start time of a generation does not move because the
        run of today read the same files. A file that is missing, broken or of
        another generation is replaced as it is.

        Args:
            current (bytes): Content of the file, None when it is missing
        """
        if current is None:
            return
        try:
            existing = self.decode(current)
        except msgspec.DecodeError:
            # a broken file is not a base, the run writes its own
            return
        if (existing.version, existing.checksum) == (self.version, self.checksum):
            self.generated_at = existing.generated_at


class LocalManifest(ManifestBase):
    """
    The local record of one generation: the files that stay on the disk.

    The file is written next to the published contract, in the workspace, and
    never leaves it: it is the record of the cache of the generator, the one a
    person reads to see which entries survive a generation and which retire.
    The published half is not repeated here, the contract next to this file
    holds it.

    Attributes:
        private (List[str]): Files that are not published, '/'-separated,
            relative to the workspace, sorted
    """

    private: List[str]

    @classmethod
    def build(cls, channel, version, checksum, private):
        """
        Build the local record of a run.

        Args:
            channel (str): Name of the workspace
            version (str): Version latest.pack serves
            checksum (str): Index pack checksum of that version
            private (Iterable[str]): Private files, see split_paths

        Returns:
            LocalManifest: The record
        """
        return cls(
            schema=LOCAL_MANIFEST_SCHEMA,
            channel=channel,
            version=version,
            checksum=checksum,
            private=sorted(private),
        )

    @classmethod
    def decode(cls, data):
        """
        Decode the content of a local record file.

        Args:
            data (bytes): Content of the file

        Returns:
            LocalManifest: The decoded record

        Raises:
            msgspec.DecodeError: If the content is not a local record of this
                schema, see _decode
        """
        return _decode(data, cls, LOCAL_MANIFEST_SCHEMA)
