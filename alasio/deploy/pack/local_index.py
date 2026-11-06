"""
The local index pack of a deploy target: the record a pack flow committed.

The index pack is the target's own file, at a path a pack flow owns
({ledger}/index.pack). It names the version of the target and the files of
it, so a flow reads it twice in the naive arrangement: once for the version
check, once for the deletion base. One read answers both, and the parse of
the index records is deferred to fileinfo(): the structure of the pack (the
header, the bounds of its sections) names the version without it, so a check
that only compares versions pays about 0.01 ms instead of the parse of every
record (about 60 ms for five thousand records).

The file is the only record of the target the jobs read on their own. Facts
that live outside the target -- the files of a python distribution installed
by pip, which the deploy format keeps no ledger of -- are supplied by the
caller as a LocalSide, see alasio.deploy.pack.local_side.
"""

from alasio.deploy.pack.decode_base import PackDecodeBase, PackDecodeError
from alasio.ext.path.atomic import atomic_read_bytes
from alasio.logger import logger


class LocalIndex:
    """
    The local index pack of a target, read once and shared by the flow.

    Attributes:
        decoder (PackDecodeBase | None): The decoded index pack, None when the
            target has no usable one
        reason (str): Why there is no decoder, '' when there is one
        missing (bool): Whether the target has no index pack at all, as
            opposed to one that can not be decoded: a target pack flows wrote
            and a target they never touched are different, see
            alasio.deploy.pack.local_side
    """

    def __init__(self, target, decoder=None, reason='', missing=False):
        """
        Args:
            target (DeployTarget): Target the index pack belongs to, its
                localize() maps the records
            decoder (PackDecodeBase, optional): The decoded index pack.
                Defaults to None
            reason (str): Why there is no decoder. Defaults to ''
            missing (bool): Whether the file does not exist at all. Defaults
                to False
        """
        self._target = target
        self.decoder = decoder
        self.reason = reason
        self.missing = missing
        # the file records, built by the first fileinfo()
        self._fileinfo = None

    @classmethod
    def read(cls, target):
        """
        Read the local index pack of a target.

        Args:
            target (DeployTarget): Target to read

        Returns:
            LocalIndex: The index pack, or the reason there is none
        """
        try:
            data = atomic_read_bytes(target.index_file)
        except FileNotFoundError as e:
            # never updated by a pack flow, which is not an error
            return cls(target, reason=str(e), missing=True)
        except OSError as e:
            # a directory, a file that can not be read
            return cls(target, reason=str(e))
        try:
            return cls(target, decoder=PackDecodeBase(data))
        except PackDecodeError as e:
            return cls(target, reason=str(e))

    @property
    def version(self):
        """
        Returns:
            str: Version the index pack records, '' when there is none
        """
        if self.decoder is None:
            return ''
        return self.decoder.current_version

    @property
    def exists(self):
        """
        Returns:
            bool: Whether the target has a decodable index pack
        """
        return self.decoder is not None

    def fileinfo(self):
        """
        The records of the index pack, the deletion base of a flow.

        The checksum is validated here: a ledger that does not describe the
        target is no deletion base, and nothing is removed on the strength of
        it (the deletion records of a pack then fail their own check and the
        flow falls back to a rebuild).

        Returns:
            dict[str, IdxInfo]: Records keyed by the local path of the target,
                deleted markers (edit == 2) excluded. The same dict on every
                call: a caller must not modify it.
        """
        if self._fileinfo is None:
            self._fileinfo = self._build()
        return self._fileinfo

    def _build(self):
        """
        Returns:
            dict[str, IdxInfo]: The records, {} when the index pack is corrupt
        """
        try:
            self.decoder.validate_index()
        except PackDecodeError as e:
            logger.warning(f'Failed to read the old index pack: {e}')
            return {}
        # The pack area of the records (.pack/**) is mapped into the ledger
        # folder of the target. localize() is not idempotent (a second pass
        # turns .pack/{name}/x into .pack/{name}/{name}/x), so it runs once,
        # here, on the records this reader owns
        self._target.localize(self.decoder)
        return {
            path: info
            for path, info in self.decoder.fileinfo.items()
            if info.edit != 2
        }
