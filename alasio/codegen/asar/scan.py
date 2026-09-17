"""
The sequential scan of the data area, the engine behind ``extract_all()``.

The entries that are stored in an archive are merged into regions of the data
area which are processed in offset order, so the data area is read once from
start to end: no seek, no content kept between regions, and the memory usage is
bounded by ``region_budget`` (or by ``chunk_size`` for a larger region). Entries
that share their content, that share a prefix or that are nested inside each
other therefore share a single read.
"""
from alasio.ext.path.atomic import CHUNK_SIZE

from .errors import AsarFormatError
from .pack import AtomicChunkWriter

# Regions up to this size are read in one piece, it is about the cost of one
# seek on a mechanical disk (~1 MB of sequential reading)
REGION_BUDGET = 1048576


class Member:
    """
    One entry inside a region of the data area.

    The offsets are absolute, they point into the archive file, so a region is
    ready to be read without knowing where the data area starts.
    """
    __slots__ = ('start', 'end', 'path', 'target', 'verifier', 'mode', 'writer')

    def __init__(self, start, end, path, target, verifier=None, mode=None):
        """
        Args:
            start (int): Offset of the content in the archive
            end (int): Offset just after the content in the archive
            path (str): Archive path of the entry, only used in error messages
            target (str): Path of the file to write
            verifier (ContentVerifier): Checker of the content, None to write as is
            mode (int): POSIX mode of the target file, None to keep the default
        """
        self.start = start
        self.end = end
        self.path = path
        self.target = target
        self.verifier = verifier
        self.mode = mode
        self.writer = None

    def __repr__(self):
        return f'Member({self.path!r}, {self.start}, {self.end})'


class Region:
    """
    A part of the data area that is read in one go.
    """
    __slots__ = ('start', 'end', 'members')

    def __init__(self, start, end, members):
        """
        Args:
            start (int): Offset of the region in the archive
            end (int): Offset just after the region in the archive
            members (list): ``Member`` list, sorted by start offset
        """
        self.start = start
        self.end = end
        self.members = members

    def __repr__(self):
        return f'Region({self.start}, {self.end}, {len(self.members)} members)'


def build_regions(members):
    """
    Merge the entries of the data area into regions.

    Entries that overlap or touch each other are merged, so that the content of
    duplicated entries (4.3.0 writes identical content once), of entries that
    share a prefix and of nested entries is read once. Entries of a real archive
    are stored back to back, so the whole data area is usually a single region.

    Args:
        members (list): ``Member`` list, sorted by start offset

    Returns:
        list: ``Region`` list, in offset order
    """
    regions = []
    for member in members:
        if regions and member.start <= regions[-1].end:
            region = regions[-1]
            if member.end > region.end:
                region.end = member.end
            region.members.append(member)
        else:
            regions.append(Region(member.start, member.end, [member]))
    return regions


def write_member(member, content):
    """
    Write a member from a buffer.

    Args:
        member (Member): Member to write
        content (memoryview): Whole content of the member
    """
    member.writer = AtomicChunkWriter(member.target, mode=member.mode)
    if len(content):
        member.writer.write(content)
        if member.verifier is not None:
            member.verifier.update(content)
    finish_member(member)


def finish_member(member):
    """
    Close the file of a member and check its content.

    Args:
        member (Member): Member whose content was fully written

    Raises:
        AsarError: If the content does not match the header
    """
    member.writer.close()
    member.writer = None
    if member.verifier is not None:
        member.verifier.check()


def scan_regions(fd, regions, region_budget=REGION_BUDGET, chunk_size=CHUNK_SIZE):
    """
    Read every region once and write the content of its members.

    The handle has to be positioned on the first byte of the first region: a
    region that does not start where the previous one ended is the only reason
    to seek, and the entries of a well formed archive are stored back to back,
    so the whole data area is usually a single region and a real archive is read
    without a single seek.

    Args:
        fd (io.IOBase): Open handle of the archive
        regions (list): ``Region`` list in offset order, see ``build_regions()``
        region_budget (int): Regions up to this size are read in one piece, the
            others are streamed, 0 streams everything
        chunk_size (int): Read chunk size of a streamed region

    Raises:
        AsarFormatError: If the archive is truncated
        AsarError: If a content does not match the entry it belongs to
    """
    position = regions[0].start
    for region in regions:
        length = region.end - region.start
        if region.start != position:
            # Only entries that are not back to back need this, a real archive
            # never does
            fd.seek(region.start)
        if length <= region_budget:
            # The whole region fits in the budget, read it once and slice it
            buffer = fd.read(length)
            if len(buffer) != length:
                raise AsarFormatError(
                    f'Archive is truncated, expected {length} bytes but got {len(buffer)}'
                )
            position = region.end
            view = memoryview(buffer)
            for member in region.members:
                write_member(member, view[member.start - region.start:member.end - region.start])
            continue
        # Larger than the budget, stream it and feed every member that overlaps
        # the current chunk
        members = region.members
        active = []
        index = 0
        offset_in_region = 0
        while offset_in_region < length:
            take = min(chunk_size, length - offset_in_region)
            chunk = fd.read(take)
            if len(chunk) != take:
                raise AsarFormatError(
                    f'Archive is truncated, expected {take} bytes but got {len(chunk)}'
                )
            chunk_start = region.start + offset_in_region
            chunk_end = chunk_start + take
            position = chunk_end
            view = memoryview(chunk)
            while index < len(members) and members[index].start < chunk_end:
                member = members[index]
                member.writer = AtomicChunkWriter(member.target, mode=member.mode)
                active.append(member)
                index += 1
            remaining = []
            for member in active:
                piece_start = max(chunk_start, member.start)
                piece_end = min(chunk_end, member.end)
                if piece_start < piece_end:
                    piece = view[piece_start - chunk_start:piece_end - chunk_start]
                    member.writer.write(piece)
                    if member.verifier is not None:
                        member.verifier.update(piece)
                if member.end <= chunk_end:
                    finish_member(member)
                else:
                    remaining.append(member)
            active = remaining
            offset_in_region += take
