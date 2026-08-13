import zlib

import pytest

from alasio.git.file.exception import ObjectBroken
from alasio.git.obj.objtag import TagObject, parse_tag, parse_tag_object

# Real tag objects from real repositories, zlib-compressed.
# AzurLaneAutoScript v2020.04.08  tag sha1 b408a075f13681edd44fcbb17d452bdd8aaf67aa
obj_azurlaneautoscript_2020_04_08 = zlib.decompress(
    b'x\x9c\x15\x8c\xc1\n\xc20\x10\x05\xef\xf9\x8a\xbd\x0b\xe5'
    b'%\xa6\xdb\x04D\xfc\x00o~\xc16]K$\xb1b'
    b'\x83\xa0_o{\x9b9\xcc,\xe3CS\xa3$\x1a\x07'
    b'v#\xf7l\x05\xe9\x1e\x06\xc6q\x8a\x1a\x01\x01\x06\x1b'
    b'&\xf1\x16,\xa6}_Ji\xa957\xd3d\xa6\x8f'
    b'\x83C\x07\xdf!\xec>\xeb\x9b\xaeUo\xbf\xfcLt'
    b'*U\xd7\x9dV)\xba^\xe6*\xb9t[{&\xdb'
    b'\x07\xde~\xd63\x1d\x10\x00c\xfe\xef\xf6(;'
)
# bilibili-live-chat v1.0.0  tag sha1 22ef058d160dea3a98da2cacd5276a1dc7d2e62f
obj_bilibili_live_chat_1_0_0 = zlib.decompress(
    b'x\x9c-\xcbA\n\xc20\x10\x00\xc0{^\xb1w!'
    b'\xec\xda\x86n@\xc4\xaf4\x9bm\x894D$\x14|'
    b'\x81\x9f\x10/\xe2\x17<\xe8w\x94~C\x04os\x99'
    b'\x12\xf6*\x15"7\x12\xb8\x8d\xa1\x97\x10\x99D\xbd\x0e'
    b'\x8c\x9d\xb4kR\x1fE\x94\xe2\xe0\x1cySO\x07\x05'
    b')9\xa7jj?\xc2L\x16-\xfe8\xea\x11\x96\xfb'
    b'\xf5\xfd\xba-\x8f\xe7\xe7|\x81M\xdaMeJRl'
    b'.\xba\x05r\x0e\xb1\xa1\x8e[X!#\x1a\xf3\xbf_'
    b'\x87C+I'
)
# isort 4.3.7  tag sha1 f13b010477f777d33649cd6aa838b4ce8e148db2
obj_isort_4_3_7 = zlib.decompress(
    b'x\x9c%\x8cA\n\xc3 \x10\x00\xef\xbeb?P\xd1'
    b'\xe8\x1a\x17J)\xf4\x0b\xfd\x80.j-\x11C\xea%'
    b'\xbfo\x8b\xb7a`\xa6\xc7w\xe2\x01\x8b\xb1\x8c\x86\x98'
    b'|\xa0\x10]t\xb8\xa23\x91c\xd6\x990;Kq'
    b'\t&\xb0\x18\xe7\x9e\x80{ku\x88\x11\nXi\xe4'
    b'\xfa\xa7\x92\x0ex\xd6\xd6\xc7\xeb\x84\xc7\xd1?[:\xe1'
    b':\xa6\x90<\xc5\xbd\xb4P7\xf9\xcbo\xa0\x115z'
    b'o\x89\xe0\xa2\xbcRB\xcc\xd5\x17\xf7\x84,\x1f'
)
# structlog 16.1.0  tag sha1 e05180b089efda58eed5f9e2792d7cac341994e4
obj_structlog_16_1_0 = zlib.decompress(
    b'x\x9cm\x93\xc9\xb2\xa2H\x00E\xf7|\x85{\xa3\x1e'
    b'C&HFTuT2\x982(\x83\x08\xc8\x0e\x94'
    b'AAd\x92\xe9\xeb\xab^\xf7\xb6\xef\xea\xc6]\x9c\x88'
    b'\xbb8\xef\xe4\x99\xde\x86M\x0cP& F\x889A'
    b'\xcc\x12.\x837 \xf0\x0c\xe4\x12\xc0\xde\x19\x8ea\xe0'
    b'\xf7\x0c\x11G\rK\x93nn\xef\xd7\xeb1PC\x9c'
    b'oX\xe1\x8b\xfdb\xbek\x9ev\x9b\xc3R\xa7\xe5\xe6'
    b'|+\xaax\x8ao\xe5\xe6g\xd1\xff~\xcf_\xb7\xf9'
    b'\x9f\r\x0b\x05\xc8\xfe\x05\xa1\xddf\xfbM\xa4\xa81\xed'
    b'\xfa\xc7\xbb\xa6~|GR\x89v\xda\xd8\xc4\xde\x9c5'
    b'r\xc2\xde\xc5U\xff\xdd)\xea\xe1h7\tcI\xc6'
    b'\x98H\x8e\x1e\xba$v1~\xeb\xaa\x01\xab\xd3C\xa7'
    b'\xe3\x8cHe(:X\xf7\x9c\xc8\xb5&6\xdf\x9f\xad'
    b'&V2M\xb6\x1a\x9a%\xd4\xa7\xf2t\x1d\xcfw\xbf'
    b'\xed\x8c\xfc\xccu\xe4P\x97S\t\x1a\xff\xac\xb4\x0e\n'
    b'\x10\xfa\x1cR\xa5\xe1_r\xe0\xce\xdc2\x1cx\xf9\xae'
    b"\xb9\x02\xb2\x9f\x9d_'0\xc7\xd4\xd4\x0b\xa9 \xab\xc5"
    b'\xde\xaf\xf6e\x00\xc0\xa8\xe4qk"\x9e\xb8\xc6:\x8f'
    b'A\xaf\xf6R\xe0\xda\x1f0D%8\xaa\xfe0lk'
    b"\x9a^\xe8\x9a\xe0\x03C\xc09\xa4\xe8\x034\x92'q"
    b'\x03\xd5\xac\xd5\x8f\xb6G\xb5\xea\xc7\xa1\x08\xce\xca\x9a\xbd'
    b'\xf6~\xdf\xf9\xf7\x8fK\xb7\xfaR1\x86x(\x1b\xec'
    b'\xabY\x8egEw\x17\xa3\xd5\x9c\x80\x12\x9fG\x9f\xc9'
    b'T\x03\xf4U`\x97\x9a\xb6\x8d\xaeM\xe8\x96\xe9S\xdf'
    b'\xaf\x95t8\xd27\xae6\x13e\t\x13\xd5_Vb'
    b'j\x8boN\xd7^\x82@\x9a\xef\xdcTQ@\x02m'
    b'JK"\xfa\xfb\xa3\x19.\x9a|\xd9*|^_\x91'
    b'\x13\xbc\x8cm\x9f\xe4L\x1c\xdbn|\xb1\x1efkx'
    b'\xe9\xf1\xc1\xbcm\x85@u\xfb\x18G$v\x80\x1a['
    b'\x98\xc5\x02<:\t\x17nw#>v\xe3\x8a=\xc4'
    b'w\xfbeR\x8a\xae\x833b\xa4\xc5\xb9\xb4\x07\xdb|'
    b'f\xbe\xbc\xf0\x06\xc9_\xc1\xce,\x9fi1K\x0f\xea'
    b'S\x8e\xec\xb9\xfc`tUw#m%\xbal\xae\xf5'
    b'u\xc9\x93r\xf2\x1a;\xe3\xc9\xd3"\xbc:4\xc7\xe0'
    b'B\x0e\xf7^\xec\x99\x10\x13\xd9\x15\xdc\xcfH\xc6\xf7\xbe'
    b'\xa5\xe28\xa4e\x08\xb6\xd9\x1c\xe5N\xbd\xe6U\xa8\xda'
    b's\xb4\r\xa3\xda\xd9\x05q\x01#s\x9a\xb2\xb5\xb0\xe6'
    b'\x1d\x8a2\xd3\xf08@\x8cw\xac\xc0Fk\xc2\xc6\xdd'
    b'\xbd(\xa7\xb4\xdc\x82q=\x9dcV\xadv\x04\xce\x13'
    b'`\x9d\x05\xd9\xb1F\xc9\xd2t\xe9U\x06k\xf2\xd2\xfc'
    b'\xb5\xc3\x17[\x0e\xe2\x8b\x1bX*\x9e{\x91d1\xbb'
    b'x\x0b\x95\xe2J\x9ci\x84\xc7\xecz:\xb3\x9dU\xaa'
    b"~\xdd>\x9a\x00'\x06\xd8\x02Y\xef\xc0\x89\x85\xe5Z"
    b'\r\xd1\x156\n0\xdc\xeeV\xcc\xcdu\xae\xf8{_'
    b'\x18\x11\xa2\xbch\xc9g\xb5\xbb=\xfa$\xe9Ce\xb5'
    b'acQ\xbfZV\x9c\xff\x93@=)\xff\xa7\xc0\x1f'
    b"\xcf\xdf'\x1b"
)
# CodeWhale v0.8.34  tag sha1 374ce5a203d07469e5a9c09b1d5d5e47b6fc2945
obj_codewhale_0_8_34 = zlib.decompress(
    b'x\x9c5R]o\xdb0\x0c|\xf7\xaf\xe0\xfb\xe2\xc0'
    b'A\xf3U`\x18\x86v\xc0:\x0c\x1b\x024\xeb\xeb '
    b'Kt\xccZ\x16\x05\x89J\xda\x7f?*\xe9^$\x81'
    b'\xba#Ow\xe2\xfe\x15\xad\xc0v\xb3\x1d\xf6\xb8\xeeW'
    b'\x1bg\xec0\xdc\x99\xddno\xb6\xe8\xb6\x83Yu\xd6'
    b'm\xbb\xcd\xda\xad\xef\xf7\xae\x91\xf7\x88`y\x9eI\x1a'
    b"1'8w\xcb\xfd\xf2n]\xcf'L\xf0T\x82\xe8"
    b'\xf6\xc0\x97\x00\x9f\xc7\xb9\xd7\xfd\xebi6\xe4\x97\xca\xf9'
    b'\x02+m\xbb\xbd\xdf\xacvkh\xbbM\xd75\xcd\x07'
    b'\x1fZ8\xb0\xa7<.@\xf93\x05\xe3\xdb\x98X\xd8'
    b'\xb2_\x80\t\x0e\xa86\xaee\xeb\xd1\x84\x12!\xa1\x1e'
    b"26\x8dR\x13\x0e\xf4\xd6ZcG\x84,\xa6'O"
    b'\xf2\x0e\x92\x8c\x9d(\x9c\x94\x0b\x03\xb3\xf2\x15\xfbP\x82'
    b'\xf3\xe8\xe0\x1bb|F\x9c\xda`\x84\xce\x08\x17N\xd3'
    b'\xe0\xf9\x02y"\xef\xb3"\x7f\xdf.~\x92h\xafO'
    b'\xf0}\xe4\\O\x81\x85\x06\xb2z\xc9\x01\xfek\xac\xf8'
    b'\xe3\x883B$;\xa9\x03\x17\x92\x11fNZH\x98'
    b'Q*\xe0q,a\xd2\xd1\xd1$\xe3=\xfa6\x9b\x01'
    b'A\x98=\xe0\x1b\xdaR;V\x98\tV/\x15\x02y'
    b'D]_\xb9\xaf\xf4g\xcc\xb9\xce\x14\x12\x8f\xf5Mj'
    b'i\xe4\xac\xc3zN\xee\xfa8t$\x7f\x07\xf2\xb5\xab'
    b'\xc7d\x043hb|J&\x8ed!\x96`\xa5\xdc'
    b'\xa4\xbbD\x83T\xd9\x7f~@\x8ejX\xb5\x98\xd5('
    b'[\xb2\x8a\x9c\xd9\x15\x8f\xd7\xb9\x92\x8a\xb2\x92\x16\xab\xa3'
    b'\xd5\xd0\xaa\xa2\xcav\x94\xa3\x11;*\xea\xd7\xe3\x01\x9e'
    b'\x8e\xc7C\xc5\x84\x1c9\xc9U\x9f\xce\xfaH\x83f5'
    b'\xeb\xac\x16\x85\xab\x19/&\x11\x97\x0c\x1a\x9c\x8a\x1c8'
    b'\x81)\xa2\xf1\xeb\xcam\xe2\xa2i-4c\x939\xd4'
    b'\x89\x965\xff \x9an\xa2\x18\xb5\xb2\xf80G\x91\xb1'
    b'\xc8\xa2\xda\xac\xad\xe0\xfa\x07n?\xa6\xba\xdf\xfc\x03W'
    b'h\x01\xc5'
)
# msgspec 0.18.6  tag sha1 9ed5e0d4f6e47e6f520835605bb647f234e7f6f3
obj_msgspec_0_18_6 = zlib.decompress(
    b'x\x9cmP\xdbn\x82@\x14|\xe7+\xf6\xbdQw'
    b'\xb5\xac\x90\xb4M\x95{\xd5\xa5\xb2h\x957Xn+'
    b"(\x8ax\xfd\xfa\xb26\xedS'9\xc9\xcc$s2"
    b'\x99*\xda$\xac\x012\x82\xf13D\x182\x19\xa9j'
    b'\x1a\xc9\xb8\x1f1\x9c*\nLP?\x1d \x16cu'
    b'\xc8p(5\xb7}\x02X\xb5\xdd\xf2Fj\xc2\x0c\xc0'
    b'.R\xbaX\xd0,\xa9\xc1\x07\xdf\x02\xad\xe6\xc7\xa6c'
    b'\x875O\xc1\xcb\x86\t\x95\x0b\xf1\x9emC^v\xdb'
    b'\xec\x1b@C(+\xaa\xac"\x05t \x86P\x92\x96'
    b'I}\xe4\xd5\xee\xf7_G`lX\x0e\x01\x94\xda\x80'
    b':\x16\x19\xf9\x0b\xcfx\xf8\xd2\x02\x11g\x01K{$'
    b'0oO\x9f\t6e\x03RM\xbf\x96\xc5l\xb3@'
    b"3\xbf\x10^\xb6\xaa'W\x93\x18\xd7\xc8,3\xf3\xb6"
    b'\xc92\xee\x87+\xd5=\xb8\x13i\xe2\x1c\xfb\xfa`\xe2'
    b'F\xa6\x11\xd7EC\x99\x08\xe8A\xbf\x84\xa3?\x04\xf7'
    b'\xd02\xdagN\xcb\xc7\xbep\xb4\x01\xb9\x87\x1a*\x03'
    b'\xddA\xc47d\xe9Q\xc2\xecE\x9aw\xb8\xd2<%'
    b'\xd8\xe9Y\xd7\xdd:7\xdb\x02\x85].u\xd7\xa6\x94'
    b'\xca^r\xe1\xe7S^?9<\x0e\x0c\xcf\xd5\xd7A'
    b'\xb5>\xaf\x9e\xf6\x1a\xde\xef+(]\xbcOo\x96G'
    b'\xf1\xc9\xb78m\x8a\x83\xe6\x99s\xf8\xfa\xb3\x84A\xf4'
    b'\xffv\xf8\x06\xb7\x07\x84D'
)


def build_tag_data(tagger_line=b'tagger LmeSzinc <lmeszincsales@gmail.com> 1585635715 +0800', message=b'Tag message'):
    """
    Build a fake tag object bytes for testing.

    Args:
        tagger_line (bytes): tagger header line without trailing newline
        message (bytes): tag message

    Returns:
        bytes:
    """
    return (
        b'object 2b07ca1800c558861022911371ce84a6e7116941\n'
        b'type commit\n'
        b'tag v1.0\n'
        + tagger_line +
        b'\n\n'
        + message
    )


class TestParseTagObject:
    def test_parse_object_success(self):
        """Test getting object sha1 from a tag object."""
        data = build_tag_data()
        obj = parse_tag_object(data)
        assert obj == "2b07ca1800c558861022911371ce84a6e7116941"

    def test_parse_object_missing(self):
        """Test handling when data does not start with "object"."""
        data = build_tag_data().replace(b'object ', b'nottree ')
        with pytest.raises(ObjectBroken) as excinfo:
            parse_tag_object(data)
        assert 'should startswith "object"' in str(excinfo.value)

    def test_parse_object_invalid_sha1(self):
        """Test handling when object sha1 can not be decoded."""
        data = b'object \xff\xfe\n'
        with pytest.raises(ObjectBroken) as excinfo:
            parse_tag_object(data)
        assert 'Tag object of tag is not a sha1' in str(excinfo.value)


class TestParseTag:
    def test_parse_tag_success(self):
        """Test parsing full tag object with all fields."""
        tag = parse_tag(build_tag_data())

        assert isinstance(tag, TagObject)
        assert tag.object == "2b07ca1800c558861022911371ce84a6e7116941"
        assert tag.type == "commit"
        assert tag.tag == "v1.0"
        assert tag.tagger_name == "LmeSzinc"
        assert tag.tagger_email == "lmeszincsales@gmail.com"
        # tagger 1585635715 +0800, tz is in minutes
        assert tag.tagger_tz == 480
        assert tag.tagger_time == 1585635715 + 480 * 60
        assert tag.message == "Tag message"

    def test_parse_tag_negative_tz(self):
        """Test parsing tag object with negative timezone."""
        tagger_line = b'tagger LmeSzinc <lmeszincsales@gmail.com> 1585635715 -0430'
        tag = parse_tag(build_tag_data(tagger_line=tagger_line))

        assert tag.tagger_tz == -270
        assert tag.tagger_time == 1585635715 - 270 * 60

    def test_parse_tag_no_prefix_tz(self):
        """Test parsing tag object with timezone without prefix."""
        tagger_line = b'tagger LmeSzinc <lmeszincsales@gmail.com> 1585635715 0430'
        tag = parse_tag(build_tag_data(tagger_line=tagger_line))

        assert tag.tagger_tz == 270
        assert tag.tagger_time == 1585635715 + 270 * 60

    def test_parse_tag_missing_object(self):
        """Test handling when object key is missing."""
        data = build_tag_data().replace(b'object ', b'nottree ')
        with pytest.raises(ObjectBroken) as excinfo:
            parse_tag(data)
        assert 'should startswith "object"' in str(excinfo.value)

    def test_parse_tag_invalid_object_sha1(self):
        """Test handling when object sha1 can not be decoded."""
        data = b'object \xff\xfe\ntype commit\ntag v1.0\n'
        data += b'tagger Test <test@example.com> 1585635715 +0800\n\nmessage'
        with pytest.raises(ObjectBroken) as excinfo:
            parse_tag(data)
        assert 'Tag object of tag is not a sha1' in str(excinfo.value)

    def test_parse_tag_missing_type(self):
        """Test handling when type key is missing."""
        data = build_tag_data().replace(b'type commit\n', b'wrong type\n')
        with pytest.raises(ObjectBroken) as excinfo:
            parse_tag(data)
        assert 'should have "type"' in str(excinfo.value)

    def test_parse_tag_invalid_type_encoding(self):
        """Test handling of tag type with invalid encoding."""
        data = build_tag_data().replace(b'type commit\n', b'type \xff\xfe\n')
        with pytest.raises(ObjectBroken) as excinfo:
            parse_tag(data)
        assert 'Failed to decode tag type' in str(excinfo.value)

    def test_parse_tag_missing_tag(self):
        """Test handling when tag key is missing."""
        data = build_tag_data().replace(b'tag v1.0\n', b'wrong v1.0\n')
        with pytest.raises(ObjectBroken) as excinfo:
            parse_tag(data)
        assert 'should have "tag"' in str(excinfo.value)

    def test_parse_tag_invalid_tag_name_encoding(self):
        """Test handling of tag name with invalid encoding."""
        data = build_tag_data().replace(b'tag v1.0\n', b'tag \xff\xfe\n')
        with pytest.raises(ObjectBroken) as excinfo:
            parse_tag(data)
        assert 'Failed to decode tag name' in str(excinfo.value)

    def test_parse_tag_missing_tagger(self):
        """Test handling when tagger key is missing."""
        data = build_tag_data().replace(b'tagger', b'wronger')
        with pytest.raises(ObjectBroken) as excinfo:
            parse_tag(data)
        assert 'has no "tagger"' in str(excinfo.value)

    def test_parse_tag_invalid_tagger_name(self):
        """Test handling of tagger name with invalid encoding."""
        tagger_line = b'tagger \xff\xfeTest <test@example.com> 1585635715 +0800'
        data = build_tag_data(tagger_line=tagger_line)
        with pytest.raises(ObjectBroken) as excinfo:
            parse_tag(data)
        assert 'Failed to decode tagger name' in str(excinfo.value)

    def test_parse_tag_invalid_tagger_email(self):
        """Test handling of tagger email with invalid encoding."""
        tagger_line = b'tagger Test <\xff\xfetest@example.com> 1585635715 +0800'
        data = build_tag_data(tagger_line=tagger_line)
        with pytest.raises(ObjectBroken) as excinfo:
            parse_tag(data)
        assert 'Failed to decode tagger email' in str(excinfo.value)

    def test_parse_tag_invalid_tagger_time(self):
        """Test handling of invalid tagger timestamp."""
        tagger_line = b'tagger Test <test@example.com> notanumber +0800'
        data = build_tag_data(tagger_line=tagger_line)
        with pytest.raises(ObjectBroken) as excinfo:
            parse_tag(data)
        assert 'Tagger time is not int' in str(excinfo.value)

    def test_parse_tag_invalid_tagger_timezone(self):
        """Test handling of invalid tagger timezone format."""
        tagger_line = b'tagger Test <test@example.com> 1585635715 invalid'
        data = build_tag_data(tagger_line=tagger_line)
        with pytest.raises(ObjectBroken) as excinfo:
            parse_tag(data)
        assert 'Failed to parse tagger timezone' in str(excinfo.value)

    def test_parse_tag_invalid_message(self):
        """Test handling of tag message with invalid encoding."""
        data = build_tag_data(message=b'\xff\xfeTag message')
        with pytest.raises(ObjectBroken) as excinfo:
            parse_tag(data)
        assert 'Failed to decode commit message' in str(excinfo.value)


class TestParseTagReal:
    """Test parsing real tag objects from real repositories."""

    def test_azurlaneautoscript_plus0800_empty_message(self):
        """AzurLaneAutoScript v2020.04.08, +0800 tz, empty message."""
        tag = parse_tag(obj_azurlaneautoscript_2020_04_08)

        assert tag.object == "cae9762b6561a0cf87603d9e900a00718da4106a"
        assert tag.type == "commit"
        assert tag.tag == "v2020.04.08"
        assert tag.tagger_name == "LmeSzinc"
        assert tag.tagger_email == "lmeszincsales@gmail.com"
        assert tag.tagger_tz == 480
        assert tag.tagger_time == 1586410146 + 480 * 60
        assert tag.message == ""

    def test_bilibili_live_chat_utf8_tagger_name(self):
        """bilibili-live-chat v1.0.0, +0800 tz, UTF-8 tagger name."""
        tag = parse_tag(obj_bilibili_live_chat_1_0_0)

        assert tag.object == "d83cb84dbacbd81ce9ef807c421e9dcce1df5519"
        assert tag.tag == "v1.0.0"
        assert tag.tagger_name == "神代綺凜"
        assert tag.tagger_email == "i@lolico.moe"
        assert tag.tagger_tz == 480
        assert tag.tagger_time == 1550031784 + 480 * 60
        assert tag.message == "v1.0.0"

    def test_isort_minus0800(self):
        """isort 4.3.7, -0800 tz."""
        tag = parse_tag(obj_isort_4_3_7)

        assert tag.object == "234c539c98a9ab6b657563bcbf1f95f649b2a3ac"
        assert tag.tag == "4.3.7"
        assert tag.tagger_name == "Timothy Crosley"
        assert tag.tagger_email == "timothy.crosley@gmail.com"
        assert tag.tagger_tz == -480
        assert tag.tagger_time == 1551588499 - 480 * 60
        assert tag.message == "4.3.7"

    def test_structlog_plus0200_pgp_signature_message(self):
        """structlog 16.1.0, +0200 tz, message contains a PGP signature block."""
        tag = parse_tag(obj_structlog_16_1_0)

        assert tag.object == "a39f6906a268fb2f4c365042b31d0200468fb492"
        assert tag.tag == "16.1.0"
        assert tag.tagger_name == "Hynek Schlawack"
        assert tag.tagger_email == "hs@ox.cx"
        assert tag.tagger_tz == 120
        assert tag.tagger_time == 1464100497 + 120 * 60
        assert tag.message.startswith("version\n-----BEGIN PGP SIGNATURE-----\n")
        assert tag.message.endswith("-----END PGP SIGNATURE-----")

    def test_codewhale_minus0500_multiline_message(self):
        """CodeWhale v0.8.34, -0500 tz, multiline release message."""
        tag = parse_tag(obj_codewhale_0_8_34)

        assert tag.object == "656f8e4b15dacff3a778a6ed6fa10cd6054d498d"
        assert tag.tag == "v0.8.34"
        assert tag.tagger_name == "Hunter Bown"
        assert tag.tagger_email == "hmbown@gmail.com"
        assert tag.tagger_tz == -300
        assert tag.tagger_time == 1778695174 - 300 * 60
        assert tag.message.startswith("v0.8.34 - Polish, terminal-protocol")
        assert "Cancel-all shell jobs" in tag.message
        assert len(tag.message.splitlines()) == 14

    def test_msgspec_minus0600_ssh_signature_message(self):
        """msgspec 0.18.6, -0600 tz, message contains an SSH signature block."""
        tag = parse_tag(obj_msgspec_0_18_6)

        assert tag.object == "510d40160c5199fb562bc6f880e12f31cd697c6a"
        assert tag.tag == "0.18.6"
        assert tag.tagger_name == "Jim Crist-Harif"
        assert tag.tagger_email == "jcristharif@gmail.com"
        assert tag.tagger_tz == -360
        assert tag.tagger_time == 1705895918 - 360 * 60
        assert tag.message.startswith("Version 0.18.6\n-----BEGIN SSH SIGNATURE-----")
        assert tag.message.endswith("-----END SSH SIGNATURE-----")
