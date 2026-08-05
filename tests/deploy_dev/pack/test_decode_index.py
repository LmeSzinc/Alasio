"""
Tests for PackDecodeBase on index packs: no data section.

Index packs carry only the index section (header + index section), the
file data lives in the full pack. The decoder must load the index
correctly, but any data access (validate_data, catdata, catfile) must
raise PackDecodeError.
"""
import pytest
from conftest import COMMIT, WEBSITE_FULL_PACK, WEBSITE_INDEX_PACK

from alasio.deploy.pack.decode_base import PackDecodeBase, PackDecodeError


class TestPackDecodeIndex:
    """Index pack decode: index loads, data access raises."""

    def test_structure(self):
        """Index pack decodes the header with an empty data section."""
        decoder = PackDecodeBase(WEBSITE_INDEX_PACK)
        assert decoder.pack_version == b'\x00'
        assert decoder.version == COMMIT
        assert isinstance(decoder.data_section, memoryview)
        assert len(decoder.data_section) == 0

    def test_validate_index_passes(self):
        """Index checksum must validate."""
        decoder = PackDecodeBase(WEBSITE_INDEX_PACK)
        decoder.validate_index()  # must not raise

    def test_validate_data_raises(self):
        """validate_data on an index pack must raise."""
        decoder = PackDecodeBase(WEBSITE_INDEX_PACK)
        with pytest.raises(PackDecodeError, match='no data section'):
            decoder.validate_data()

    def test_validate_raises(self):
        """validate combines both checks, the data part must raise."""
        decoder = PackDecodeBase(WEBSITE_INDEX_PACK)
        with pytest.raises(PackDecodeError, match='no data section'):
            decoder.validate()

    def test_index_matches_full_pack(self):
        """The index decodes the same records as the full pack."""
        idx = PackDecodeBase(WEBSITE_INDEX_PACK)
        full = PackDecodeBase(WEBSITE_FULL_PACK)
        assert list(idx.fileinfo) == list(full.fileinfo)
        assert idx.refinfo == full.refinfo
        # every field except data_start, which is meaningless without data
        for path in idx.fileinfo:
            left = idx.fileinfo[path]
            right = full.fileinfo[path]
            for field in left.__struct_fields__:
                if field == 'data_start':
                    continue
                assert getattr(left, field) == getattr(right, field), (
                    f'{field} mismatch for {path}'
                )

    def test_catdata_raises(self):
        """catdata on an index pack must raise."""
        decoder = PackDecodeBase(WEBSITE_INDEX_PACK)
        info = decoder.fileinfo['backend/main.py']
        with pytest.raises(PackDecodeError, match='no data section'):
            decoder.catdata(info)

    def test_catfile_raises(self):
        """catfile on an index pack must raise."""
        decoder = PackDecodeBase(WEBSITE_INDEX_PACK)
        info = decoder.fileinfo['backend/main.py']
        with pytest.raises(PackDecodeError, match='no data section'):
            decoder.catfile(info)


class TestExtractIndexPack:
    """extract_index_pack must produce the index pack from any pack."""

    def test_extract_from_full(self):
        """Extracting from a full pack must yield the index pack."""
        full = PackDecodeBase(WEBSITE_FULL_PACK)
        extracted = full.extract_index_pack()
        assert isinstance(extracted, memoryview)
        assert extracted == WEBSITE_INDEX_PACK
        # the extracted pack must decode the same records as the source
        assert list(PackDecodeBase(extracted).fileinfo) == list(full.fileinfo)

    def test_extract_from_index(self):
        """An index pack must extract to itself, without error."""
        decoder = PackDecodeBase(WEBSITE_INDEX_PACK)
        assert decoder.extract_index_pack() == WEBSITE_INDEX_PACK
