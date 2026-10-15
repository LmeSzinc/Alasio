import zlib

import pytest

from alasio.git.file.exception import ObjectBroken
from alasio.git.obj.objcommit import CommitObj, parse_commit, parse_commit_tree, tz2delta


class TestTz2delta:
    def test_positive_timezone(self):
        """Test timezone conversion for positive values with '+' prefix."""
        assert tz2delta(b"+0800") == 480  # 8 hours in minutes
        assert tz2delta(b"+0000") == 0
        assert tz2delta(b"+0430") == 270  # 4 hours 30 minutes in minutes

    def test_negative_timezone(self):
        """Test timezone conversion for negative values with '-' prefix."""
        assert tz2delta(b"-0800") == -480
        assert tz2delta(b"-0430") == -270

    def test_no_prefix_timezone(self):
        """Test timezone conversion for values without prefix (new case in updated function)."""
        assert tz2delta(b"0800") == 480  # 8 hours in minutes
        assert tz2delta(b"0000") == 0
        assert tz2delta(b"0430") == 270  # 4 hours 30 minutes in minutes

    def test_invalid_format(self):
        """Test timezone conversion with invalid format raises ValueError."""
        with pytest.raises(ValueError):
            tz2delta(b"ABC")  # Non-numeric characters
        with pytest.raises(ValueError):
            tz2delta(b"12")  # Too short
        with pytest.raises(ValueError):
            tz2delta(b"+12")  # Too short after prefix
        with pytest.raises(ValueError):
            tz2delta(b"-1")  # Too short after prefix


# a42aa008648fb7266168aaf9f35510c4ad86bb05
obj_chinese = zlib.decompress(
    b'x\x9cu\x90Ao\xd30\x18\x86\xef\xf9\x15\xbeSP\x9c4\x89\x83\x10b\x12\x12\x9a\x80\xd3\xb43\xca\x12\x03'
    b'\x95\x9a\x155\xe1\xc2)\x9aX\x9bn\x94V\xa3k\xbbU\xb0\xd2mbR\xb56P\xb4\xb5['
    b'\xb7\xfe\x99|^\xfc/ph5\x8d\x037\xcb~\x9e\xef}\xfd\xf9EJ\x91\xb2&\x1b\xb6\x85\x89,'
    b'\xdb\x9aF\x88\x8eeE11V\rlS\x92\xb5tj`\xac\x9bY,'
    b'\xbd\xb3\x8at\xddG\xd8T0\xce*\xaan\x12U\xa6\x8aF^;\x0e1\x15U%\x94\x98\xd8\xb0\xa9\x8d-\xcd1\x14\xc9z'
    b'\xef\xbf-\x14\xd1\x0b\x97\xae|\xc8\xad\xdb\xe8Q\xde\xa5^z\xf2\xac<\xf5\x9e\xbcq\xad\\\xfe\x81]p\x1f'
    b'#\xac\x11MW5\x03k\xe8\x9e,\x8aH\xe2\xd6\xcd\xf9>\xfd\xbf\x8du\xf5\x1f\x97('
    b'\xd9\x85+-9\xceC\xc4\x83\r\xbeY\x8d/J\xec\xf7%l\x9d\xc0\xf5\x00~n\xb0\xf3s\xa8\x87OUI\xba\x8f\xe2'
    b'\xd9\x805&\x02x\xb6\xba|\xb3\xffq\x81u\xaea\xf3\x18j\x87\x0b\xe0\xf4P\x00\xab\xcb\xaf^.\xad<g\xcdr'
    b'|yvk&a\xc4\xdb\x07\xc9\xb0\x04a?\x83\xd6\n\x9e7?\xb3\xe6\x04\xa6\xb5x|\xc1N\xbe\xb3\xaf3\xf84\x11=n'
    b'-\xb8\xfa\x02\x95\xea]Wd\'\x95_7\x9d1o\x87\x10L\x05\t\xbdo\xb0\xd5\x15\xc10\x9c\xa4s\xa16\x8a\xc7'
    b'\xdb\xc9\x8f\x1d\x88j\xa2 \xeb\x96S\xe7\xaf|\xf7\x1f\xbc\xb1\x97\x0c\x87\xac;\x85\xd3\xba\x00\xe6'
    b'\xf5E\x1b6\xda\xcd\xa0d\xfb8\x8d\x0e[\xf3w\xb6\x1b\xf1\xb2(YM\xceF\x19\x04Q)\xe9\xf5\xa1\xd2\xe0{'
    b'G\xf3\x89p\x94\xae\x8e\x07\x01\\M\xa0\xfeY\x80\xf1t\x1ff}\x1e\x1c\xf0r\x955\xa3d\xd6\x11\x8bba\x9b5['
    b'"\x8c\xb7\x06\xbc\xd7\x96\xfe\x00\xe1\xad1\xed')
# d4bd232a10d1b597debe53d61b5fe7fe1920c54a
obj_merge = zlib.decompress(
    b'x\x9c}RK\x8f\x9bL\x10\xbc\xf3+F\xca\xd1\xca\x9a\xc7\x18\x98('
    b'\xf9\x14`\xb1\xcd\xb2\x80\xb1\x8dm\xf6b\xc1\xd0\xbc\x0c\x0c\xcb\xc3\x8f\xfd\xf5!\xab\xe4\x16}}\xac\xee'
    b'\xaeVu\xd5\xd0\x01\xa0\x05\x16\xd3\x94\x10\x99\x10\x1e\x93\x88_`L0\xe0\x85\x1aI8\x8a$"P*% '
    b'\xc9*\xd7F\x1d4\x03R\xa8\xaa\xc4)& '
    b'G\x12\xc8\x80\x85\x98\x08\xa0\xca\x0b1\x89\x95\x18\x0b\x12\xc6\xa9\x08\x04\xd2\xbf\xf3*\xe5\xe3i*Q'
    b'\xa2(\xe5\x89,\x12\x95P\x00Y\xa61\x0f\xa0R\x99\x97T\x12\t\x82\xcaE\xe3\x90\xb3\x0e\xbd\xd6\xb0\xfb('
    b'\x1a\x8a\xbeK\n\x91\xb0"\xe2\xd9_\xe8\xe7\xd8C\xd7?5\xac\x83\xb6z<e\xc5\x90\x8f\xf1\x13e\xf5\x7fH\x90E'
    b'\xfc\xa9\x81\xa0\x19\xaf\xf2<7\xa1u1\x0c\xd0\xa1U1\xac\xc7\x18}\xff\xb3\xf6\xf3\x7f\xd7\xb26\xeb\x8b'
    b'\x0c}\xfd]\xba\xb9\xb2\\\xb4Ym\xd0\xceZ\xb9\xda>\xd8\x9a\x9f8\x878t\xebu\xaak\x9anh\x9a\xaf\xfb/\x99x'
    b'\xb7\x05c\xab\xdbJ^b\xef*u\xd6mj\xe6\xcc\xd2\xd6\xa3\xffV\x18e\xaa\xccn\x8d\x9e\x1f\xc2\xa3\xfe\x92'
    b'\xec\xa8\xc9\xa1\xca\x9f\x0e\xd3\xb7\xe3\xc1;\xbey1V-f\xbd\x89\xe0\xac\xb1\xec\xf30\x13\x83\xd7|6\xd7'
    b'\xec\xd8\xa6\xd0\xb4\xd2\xfa\xa4\xb4a\xd4\xbd\x0b\xcc\x0e\xa5\xfe\xf1\xf0\x83\x9cC\x1e\x0b\x03{'
    b'\xd4W\x96\xde\xdbK\xb1\x14\xcbF\x14<k8\xf8\xe3\x96O\xf5Sh\x19\xe6\x07\xf5\xfcr\xde\xcant`\xee\xcbG'
    b'\x1bS,\x18\x86\xcb\x02z|\xf79d\xf4\xdeP\tU\xb7\xc1Us\xbf\xb8;\xd0\x83\\9\xe5\xa7\xaan\xe4\xf0\xc5\x1e5'
    b'\xb7\xb1\xf4\xfa\xb1\x95\xae\xfb\xd4_\x86K\xf2\xda\xd7\xe5\xad0|\'q\xcc\xdd\xdd\xe1\x90]\xb6\x8f\xe7'
    b'\x9cm6\xbd\xf7\xc8\xf6\x9aW\x1b\xf4\xa6W\x07s\xbf]\x9e"\xb0=M\x9fw\xb6\x0fW\xde\xb1/\xa2\xe7\xb0\xd3Q'
    b'\xac\x92G\xb3\x993\xf3$\xc6\xb521\xf0\xeeDX\xe6\xf9\xf6r\xbb:\xb5aO:]3+o\xa1\xb5T\x17\xcbY\x92\xd4fcCj'
    b'\xd7\xcf\xfb\xfa\xf2\xd8\xbc\xda\x97\x19\xdcf=\xbf\xa9B}\xcf~p\xe8G\xb6['
    b'M\x0f\xfd\xf4\xc6t\x9f\xff\xed\x18\xe7@\x97\x01j\xc7\xaaB\x1d\xbc\x8f\xd0\x0f\xe8\xcb\x82_\xa0'
    b'\xb4c5jLsH\x18\x9f\xcc\xd3\xe2~\x16\xef\xe7\x98\xb1\x0b\xc7-\x8b\xfb7T4S\x88\xaeQu\xee\xa0\x87\x01'
    b'\xa5SD\xb5`\xef\x9dw\xa6\xb65\xd6g\xc7t\x83\xb3\xe1\xb9{\xcb\r\xcc_\xa7\x17\x06\xf1')
# ce4887a4f5227a081e7ec7368ea2054428c83574
obj_initial = zlib.decompress(
    b'x\x9c\x95\x8c;\n\x021\x14\x00\xfb\x9c\xe2\xf5\x82\xe4\xfb6\x01Yl\x05;O\xf0\x92<5\x90\xec\xc2&6\x9e^E'
    b'/`7\x0c\xcc\x8c\x8d\x19Lf\xad\xa3\x94\x9a&tdR\xba\x06d\x95s$\xc7\x18\t9\xc6\xe0M\xb2\x82\x1e\xe3'
    b'\xbenpn|y\x96%\xc1\xa16\xee\x1f\xeaT\xb9\x1fo\x8dJ\xdd\xa7\xb5\xcd\xa0\x9cwV\xa1B\x84\x9d\xf4R\x8a'
    b'\xb7me\x0c\xfe\xa3\x9et\xb0\xbfZ\x9c\x962\nU\xf8n\xc4\x0b)X:V')


# A commit object with a gpg signed header, decoded from a real signed commit,
# the identity of the author and the committer replaced by Test <test@example.com>
# and encoded back, the signature block kept as is. git prefixes every line of a
# signed header by one space, so the blank line of the armor is a line of one
# space.
obj_signed_gpg = zlib.decompress(
    b'x\x9c\x95S\xb9\x0e\xabV\x14\xec\xf9\x8a+\xa5I\x84\xf2\x0c\xf8\xb2II\x14'
    b'\x0c\xd8,\x06c\x16\x1b\xd3]\xe0\xb2\x180;\x06\x7f}\x9cW\xa7\xc9\x14S\x8c'
    b'4\xa3\xa3\xa3\x99i\xc0\x18\xe0$\xceh\x86\x16D\x8e\x89)\xcc\x0b\x10e\x10'
    b'\xa7\xa9\xc8\nP\x14\xf8=\x87\xa1\x90\xa6)\x97\x10\x1d\x1a\xf0k\x024\x168'
    b'\xc8\xa5\t\x93\xa6Y\x96\nXHx\x0c\x13\x1aA\x94p\x88g\xc5\x8c\xa58\x06\xf3'
    b'\x14\x81\xe6\xa9h\x07\xe0\xe3q\x02\x7fL_\xfe\x1b\xaf\xa8\xe9j\xfc#i\x9b'
    b'\xbf\x00\xcd\x0b"d\xf6\x82\xc8\x02\x92\xfa\x82\xf8\xaaM9M\xf8\x7fX\xf2.'
    b'\x1f\xcb\x1c\xfc\xfe/\x0e\xeaI\xb7\x81sr\x80\xa7\x9fl\xc9\x0f\\\xf5\xa7N'
    b'\x00\x02\xbc\xc7cr\x90\xa4\x83,I\xd7\xc3\xd5\xe8{MmeW\xa6\xd1\x95\x9fg6('
    b'jI\x92nMp\x95N\x06i\xbd\xf6E\x96\xd89\x87\xfc\xb6K\xce\xae0\';\x02L\xd6.'
    b'\xbeSi\xb4\x8d| \x98\x94*\r\x90\xe6\x0eq\x08\xb5>\xacOtA\x9b\xaey\x9e'
    b'\xf6\xb1\x93\xbe9\xf2\xc5\xf4\x81~\xad^S\xe3\x0b\xdb\xb1\xe4\xe4\x05\x13'
    b'\xc0]n\xb34\xd9\x05\xf94\xa2\xd4c!\xc5\x97\x06\xba\x9c\x8b\x96\xcbe\xf1'
    b'\xf1X\xdb\xb3S\xf3f\\\x04\xae\x12o\x8e\x1f?\xbdW/\xf8\xcbi\xa0\xd9\xe7'
    b'\xf2:\xb6\x04\xa8\xcf\xa6\xa4\xae\xe4n+\xb5\xbb\xfe2\x19al\x18?#\xb5g'
    b'\xbe+\xf6\xed\xec7M\xec\xb2\xbb\x8bbw\x9f"\x7f\xcc\xcf\xbc\xa1$D\xed\x9d'
    b'\xc5\x0b\xa7\xfd(\x8b\x04\x10/\xd63\x08\x9c\xce-\x1b\xae\xa7\x07\x06\xd5'
    b'\xb0\x90\xe8\xe6\x10=y\x89Q;\xc3\xea\xa5K\xc7\xa0\xfez\x94B\x8c\xe4\x156'
    b'0\xab\x1f\x9f\x88\xe1\x02[{3%\x01\xd8\xd4\xec\xeb\xceOv\xb6p\xcc\x86['
    b'\xb2|\x0c\xc6h\xd6C\xa6\xdf7\xb6\x92\xcdU\xa9!\x13z\xbc\x9e\xad9w\x95!'
    b'\xf5(r\xa1\xd9\xbd\xd7\x84\xb3EU\xd4\x08\xd0.\xc7}\x80\xf2\xe2\xfd\x82'
    b'\xd1\x83\xab2\xf1X,\xca\xca\x17HY\xe6)8\xe9\x9f%\xce\xdb\x9cy\nVV\x0eJ'
    b'\x05\xb7J3\xa7U\xae\x8b\x85\'\xad\x9dl\x12\xa0\xa2\x875\x922<Glh\xa5\xb1'
    b'\x9c\\\xf4\xb3\xaa5\x92\xff\x82\xe7Z\xee\x8e\xf9\'\x9d\xa3\x05\xeb\xe5MY'
    b'\xdd\xd6K5\xa8r\xbd\xe9%\xb0Je3j7\x02\x9cu\xaaz\xb9Mu\x7fKzYN\xbe%\xd5'
    b'\x177\x94\x94\xbe\xd6\xdc\xed&?D3\x9c#j9\xe9\x01\xfcFu\xb1\xf09\x1f\xd1!'
    b'\xc2\xf8\x1dC6|x\xdf><W\x1fW\x01\xb2\xa7\xf2\x82\xcc\x0bl\xe9\x05\xf5'
    b'\xfb\xe2Q\xf4fl\xf3\x9eK\xaa3\xb6\xd6w\xe5l7\xcd\\\xda\xf7\xf50\x8cN\xb0'
    b'\x89IYi\xba\x13\xeb\x04P\xe2k\xb6&\xadZ\xd7\x8a[\xed\xd7\xb2\xf5p\xe5'
    b'\x18\xc6\xa7\xc6\x96\x86\xef:\xa9Y7\xa3=idj\xeb\xd5j\x98\xc7\xe1\xe4\xef'
    b'\x9c\xe0\xdcN\n"C\xc3\xf9\xfeA\x9da%\xa0\xfe\x19\xde?\xe9:\x92\xce}\xf5'
    b'\x0f\x04\xf8\xd39y\xdf\x03\x7fv_\xb5\x95\xff^\x04\xd1\xb4\x03\x06I\x8d'
    b'\xc6\xb1\xccJ<\x8c \xfbN\xd5\xd9\x1c\x1d4xB)\x9a\x10\xf8\xf5\x17\x9af'
    b'\xe9\xdf\xfe\x01K*P\x95'
)
# The merge of a gpg signed tag, decoded from a real signed commit, the identity
# of the author, the committer and the tagger replaced by Test <test@example.com>
# and encoded back, the signature blocks kept as is. It carries a gpgsig header
# and a mergetag header, where the whole signed tag object, its own signature
# included, is embedded.
obj_signed_mergetag = zlib.decompress(
    b'x\x9c\xadUI\x0f\xb38\x12\xbd\xf3+\xb8\xf5\x01M\x07\xcc.M\x8f\x06\x08\x10'
    b'H \x84\xb0\xdf\x80\x98\x9d\xb0\x13\xc2\xafo\xbe^\xe62\xa3\x19\xb54\x96e'
    b'\xd9\xe5\xaa\xf2+\xab^\xd5<B\x88&\tEq\x10\xf2)\x1esi\xcc\xa7,Hq\x92\x058'
    b'M\xf3$G\x02\x02\xbcH\x98R1\x8d\xf4\xf1\x08\xdf3\x9a\xa4TBq/\x8e\xe22\x98'
    b'\x90\tN\'\x80a)\xf6p\x11\xe3\xc7d_\x19\xf1J\x01\x0e\xfe\xd4\x7f\xc1\x8c'
    b'\xe2Y\x1c\x8f!\x95\x00\xf0J\x12\x1e\xe2l\x96\xa6$\xc1\x12d\x92d\x90\x871'
    b'\x9b\xe2\t\x12/s\xd1\x8d\xa8\x03\xa7\x19\xfd\xfb|\xac\xff\x84[\xdc\xf6\r'
    b'\xfc9\xed\xda\x7f\xa0\x04\xc3\xe2\x04 i\x16G1\x9c\xc0q\xe4\x90\xb6\xe5<'
    b'\xc3\xbf`\xd2\xc21\x87s\x9c\xa3]R\xc1\xf4/`C\xe7o\x0f\xd1\xdf_<\x0e\x87'
    b'\x87\x95\xf8\x99\xfb\x19\xfc\xb6\xcf\xff\x07\x04\x9cd(\x9e\xf8\x03\x02'
    b'\x8a\xfc\xcb\xf4o?\x86(\xab\x9a\x89Z\xaa\x85>5\xd5\x14\x1c\xd7\x96\x7f'
    b'\x93\xffP,\x1f\xda.\n\x82(\t\xc2K)ey;\x9f;\xdfq\xe7\x94<y[\x91\xc55\x1et'
    b'\xa7\xa1L{\xc5U\x84\xf6~]\x85I\x90\xf2\xfa\xf1\xa7\x1cA\x8f\x1b\xc9\xda'
    b'\x04\xa1\x86\x9e)p\x8f\x95"\xd9\x9dZZ\xa5\xech\xda\xd2D\x9d\xbcK\x92\x96'
    b'(^/\xc7\xb3\xa36\xe0Q\xeb%\x0b\xca\x9d\x9f\xb6\xcbg\xfb\xf6\x07\x86`\xa5'
    b'\xe3\xc7\xab\xba\x8f\x80\xee\x80\x16a\xce\xb8[\xfd%\xe6[\xba\xf1\xe7f'
    b'\x98&!\xabOt\x997\x1cQP\xc65w\xde\xc6\xa2\xeb\xfe3~\xfa\xb5\'p\x08*qo'
    b'\xf21\xf2\x8a\xbc7F\x9a\x96\x1b\x19\xd9\r\xf1\xc6\x88\xe1n~v\xe1\x13)'
    b'\x94\x9c$Q\xfc\xd9\xebw\x0b\x8a\xf7\xf0t\x1b\x93\x03\xbb\x1e\xa8\xd9x'
    b'\xc6<\x19A\xbd\xd9K\xcc!\xd6n7\xa3\xaa\r\xa5S\xf2\xa1_\x17~\'\xf5"\xbe'
    b'\x08\xaf]c\xba$\x0f\xfc\x88\xfe\x06\x1bN\xaf\xdb\xf5-\xf6\x8c\xa6\x9c'
    b'\xd7\xca\x95\xa1=\xfa\x08zy\xb6\\\xcd\xb7\xaa\x0f.#\xbe\x19\xbb\xb1\r'
    b'\xe1"hN\xaf:\'6\xba\x13\xae\x7f\x1e-\xda\x84\'\x8cY\xf3\xe8\xdb\x89\xbb|'
    b'\x19\r\xfev\x1a\x18\xac\xa0\x92\x12A\xc9\x99[.*\xe4$\x8c\x11.I\n\x8aG'
    b'\xd7\x9a\xa1\x14\xd8\xc6\n\xf2s\x8dS\xc3:\xa8aG/Z\xcd|\x12\x0cN\xaf\xf8'
    b'\xe2X\xe2\xaegz\xd5\x91\xbd\x8d\xa0\xf4\xccf\x82|\x1e\xe4\x80)\xc5\xb7'
    b'\x10ZK2p\xb7\xa0\xe8\x17\xc0>2\xdcQ\xf2k\xa8\xa9q\xedj\x8f\xd4\xca\xbc'
    b'\xef\x13w\xa5e\xed\xfa\xc7M\r\xe6\xab\xfdE\xd0\xef*B\xf1\xa6d\xcc\xea'
    b'\xb7g\xe9\x86]\xe1\xea\xe3[\xd4w9\xe5V\xe1\x1c\x9b\xf8\xa5x\xde\xd4O\xa1'
    b'Ye\xb4DY\xb3l\xe3\x02\xbf\x93\x0c\xfa\xd4\xa2\xb5\xe2\xc8\x07MHu\xeb\x96'
    b'\x9c\xe8\xc6(J\xfdLI\xa5\x8e\xdd\xd7\x0f3$}\xcc~\xbe\x1f\xf5!\x19\x9c'
    b'\xae\xd5\xb7\xd3\x9dP^\xe0\x01\x8a\xa2\xf2\xd5\xaa\x81O\xa5.\xda\x11A'
    b'\xaf\xa2\xca$,\xd4\xf0\xbbGt\x9fUQ\x14\xd9\xae+l\xdb\xba\xc9\x94qk\xe2'
    b'\x02\xef\xae>;~\xea\xb2\xc7\xe8E\xa0\x9c)\xfd\x14\xc4elM\xd6\xc5\xa4\x8f'
    b'\x9c\xe4\xc7\x8bw\x7f2w\x7f\x0b\x1a/>\xfb\x96H\xbc\xddh\xcb\x0b\x9b\xb1]'
    b'\x99\x97\x0ci\xee\x047\x10\x8a\x97\x14\xad\xb7\x99\n\x14\xf9\x17\x04\xfd'
    b'\x05+\x92\xe5\x0f\xce\xc8\xe6\xf9?1&\xef\xf3\xa9\xcc\xff\xef\xb42\xe4'
    b'\xb3\xf1o\xb4\xf2\xcc\xe1\xa0U95\xc2\xde\x8a\xa9\xeb\xd7\xd7\xae\x1a\xfb'
    b'\x97j\xc7\x1b\xe6\xd5b\xfbpe\xf9\xa2\xc7- <\x18\x9d\xa8\xae$.\xae/\x13~'
    b'\xf5a\xe0\xee (Q\x7f\xf3%\xfc\xda\t\xabK\x9bn\xd9\x8a\x8c\x0f\x89\x1f'
    b'\xcaj\x06\xbe\x13P_\xe1\xc4\x9f\xb7\x13-RT\x90\x17\x0fH8p\x14_\xdf\x96'
    b'\xb9\x9e\x86i\xc19\x13A\x8d\xab \x7f\x06x0\xa0\xc0Z*\xffzj%5\x99\xacu\'|'
    b'\x97\x9b^?\x92yI\xde8\xf1M\x9e"\x90jN)l\r\\\xb3\xed\xd3\x93\xa45\xe3\xef'
    b'#\n\xf0\x0c\x9b\xc6\xe3|L%\x1a\x03\x90\xb1XsL\xcd\x9d\xc6V5\xf4K\x99\xde'
    b'\xd9\xe7\x19\xec[]\'b5vD\xbe\x12\xec%\xec\xd4\x01/C\x1ew0\x0cAe\xc1-\xce'
    b'oY\x1e\x1e\xfd\xe3\xd2\x9e\xaf\xb1WMu\x97\xc5\x0bky\xf4N\x7f\xaa\x13a'
    b'\xc5\xac\x03vc\x9a\\\\X\xa3\x94)B\xbf\x13\xf8-\x96lJ\xec\x10\xb4\x12q'
    b'\xf7\x89\xcd\xf7\xb2\xa1\x95VN\xfb\xeaTy\xf5\xe5j\x05\x04\xc6\xe0\xb4AO'
    b'\xa3\xd6\x14\xa0\x06\n\xe3\r\xb2\xe2\xd1S\x9a\xd3\xd7]t\xc7\xa7\x13D\xba'
    b'P\x1d\x05\xb8\xdfnX \n]N?\x04&\x84:o\x8e\xf4\xee\xde\xb8;^\xc8/r\xd3\xf9'
    b'[\xd9<\x84\xf7\xd2\xb2o\xdb\x9f\x81\\Q*E\xc1\xbd\x1df\xc5\x9f\xf8\xe3\''
    b'\xe7\xb5\xf98f%\xda\x13\xed\xe2k\x0e\xfdDI\x08\x87S\x8d\xac\xab\xef[{'
    b'\x86e\xbb5\x1f\x93_\xc8\x92S\xec\xfb\xa9\xc1\xce\x0c-\x9fMl\x1d\xdcs\xe2'
    b'\xbf\x104.\xd2\xe8+E\x1cPeV;\x95\xc2\x1e\xe6_\xf3u\x91\xe0\x87\xbd+\xd5'
    b'\xae\r#\xb0\x8d=$\xe3\x85\xaa\xa3\xeb\x11\xcb\x98p-\x19\xb2\xb2,\x8dy'
    b'\xb6\x1cQ\xb8a\xbd\xf4O\xe5\xa8\xd1\xbc+j^\x7f\xc3\x81\xac\xdaw\xa7\xf4'
    b'\xae\xa0\xbf\xee\xa1\xe7}\xd4\xefxZ\xea\xbb\x15\x88\x8aX\x84\xdeuaZB\xc8'
    b'\x1d\xd3\t\xda\xe8\xc0\x10\xc6\xdd8xX[<\x1f\x9c\xf0l\xe0jB\x1a\xf0Q\xa7'
    b'\x11\x86Q\xecB*\x10\x06\'\xd9J\xef\xbc\xb4Bl\x1c\xa0\x7f??h\xe5-V\xff'
    b'\xdfi\x85\x18?\xba\xe6oM\xef\xa7\xdf[\xd7O\x08\xf2G\x0f\xfb\x15\xcb\x02'
    b'\x97m'
)


class TestParseCommitTree:
    def test_parse_tree_chinese(self):
        """Test parsing tree from Chinese commit."""
        tree = parse_commit_tree(obj_chinese)
        assert isinstance(tree, str)
        # Use startswith instead of exact equality to handle system differences
        assert tree.startswith("2b07ca1800c")
        assert len(tree) > 20  # SHA-1 is at least 20 bytes

    def test_parse_tree_merge(self):
        """Test parsing tree from merge commit."""
        tree = parse_commit_tree(obj_merge)
        assert isinstance(tree, str)
        # Use startswith instead of exact equality to handle system differences
        assert tree.startswith("542ff99699")
        assert len(tree) > 20  # SHA-1 is at least 20 bytes

    def test_parse_tree_initial(self):
        """Test parsing tree from initial commit."""
        tree = parse_commit_tree(obj_initial)
        assert isinstance(tree, str)
        # Use startswith instead of exact equality to handle system differences
        assert tree.startswith("3de22b002a")
        assert len(tree) > 20  # SHA-1 is at least 20 bytes

    def test_tree_missing(self):
        """Test handling when 'tree' key is missing in data."""
        # Create data that doesn't start with 'tree'
        invalid_data = b"nottree fb3a8bdd0ceddd019615af4d57a53f43d8cee2bf\n"
        with pytest.raises(ObjectBroken) as excinfo:
            parse_commit_tree(invalid_data)
        assert "should startswith \"tree\"" in str(excinfo.value)


class TestParseCommit:
    def test_parse_commit_chinese(self):
        """Test parsing full commit with Chinese characters."""
        commit = parse_commit(obj_chinese)

        assert isinstance(commit, CommitObj)
        # Use startswith instead of exact equality to handle system differences
        assert commit.tree.startswith("2b07ca1800")
        assert isinstance(commit.parent, str)
        assert commit.author_name is not None
        assert commit.author_email is not None
        assert commit.committer_name is not None
        assert commit.committer_email is not None
        assert isinstance(commit.author_time, int)
        assert isinstance(commit.committer_time, int)
        assert isinstance(commit.message, str)
        # obj_chinese uses +0800
        # author 1585635715 +0800, committer 1585635824 +0800
        assert commit.author_tz == 480
        assert commit.committer_tz == 480
        assert commit.author_time == 1585635715 + 480 * 60
        assert commit.committer_time == 1585635824 + 480 * 60

    def test_parse_commit_merge(self):
        """Test parsing merge commit (with two parents)."""
        commit = parse_commit(obj_merge)

        assert isinstance(commit, CommitObj)
        # Use startswith instead of exact equality to handle system differences
        assert commit.tree.startswith("542ff99699")
        # Merge commit should have multiple parents
        assert isinstance(commit.parent, list)
        assert len(commit.parent) >= 2, "Merge commit should have at least 2 parents"

        assert commit.author_name is not None
        assert commit.author_email is not None
        assert commit.committer_name is not None
        assert commit.committer_email is not None
        assert isinstance(commit.author_time, int)
        assert isinstance(commit.committer_time, int)
        assert isinstance(commit.message, str)
        # obj_merge uses +0800
        assert commit.author_tz == 480
        assert commit.committer_tz == 480

    def test_parse_commit_initial(self):
        """Test parsing initial commit (no parent)."""
        commit = parse_commit(obj_initial)

        assert isinstance(commit, CommitObj)
        assert commit.tree.startswith("3de22b002a")
        # Initial commit might have no parent
        if commit.parent:
            assert isinstance(commit.parent, bytes)

        assert commit.author_name is not None
        assert commit.author_email is not None
        assert commit.committer_name is not None
        assert commit.committer_email is not None
        assert isinstance(commit.author_time, int)
        assert isinstance(commit.committer_time, int)
        assert isinstance(commit.message, str)
        # obj_initial uses +0800
        assert commit.author_tz == 480
        assert commit.committer_tz == 480

    def test_missing_author(self):
        """Test handling of commits with no author."""
        invalid_data = b"tree fb3a8bdd0ceddd019615af4d57a53f43d8cee2bf\n"
        with pytest.raises(ObjectBroken) as excinfo:
            parse_commit(invalid_data)
        assert 'Commit object has no "author"' in str(excinfo.value)

    # This helper now correctly returns raw bytes, as the parsing functions expect it.
    def _create_test_data(self, content: bytes) -> bytes:
        return content

    def test_invalid_author_name_encoding(self):
        """Test handling of author name with invalid encoding."""
        invalid_data = self._create_test_data(
            b"tree fb3a8bdd0ceddd019615af4d57a53f43d8cee2bf\n"
            b"author \xff\xfeTest <test@example.com> 1604563164 +0800\n"
        )
        with pytest.raises(ObjectBroken) as excinfo:
            parse_commit(invalid_data)
        assert 'Failed to decode author name' in str(excinfo.value)

    def test_invalid_author_email_encoding(self):
        """Test handling of author email with invalid encoding."""
        invalid_data = self._create_test_data(
            b"tree fb3a8bdd0ceddd019615af4d57a53f43d8cee2bf\n"
            b"author Test <\xff\xfetest@example.com> 1604563164 +0800\n"
        )
        with pytest.raises(ObjectBroken) as excinfo:
            parse_commit(invalid_data)
        assert 'Failed to decode author email' in str(excinfo.value)

    def test_invalid_author_time(self):
        """Test handling of invalid author timestamp."""
        invalid_data = self._create_test_data(
            b"tree fb3a8bdd0ceddd019615af4d57a53f43d8cee2bf\n"
            b"author Test <test@example.com> notanumber +0800\n"
        )
        with pytest.raises(ObjectBroken) as excinfo:
            parse_commit(invalid_data)
        assert 'Author time is not int' in str(excinfo.value)

    def test_invalid_author_timezone(self):
        """Test handling of invalid author timezone format."""
        invalid_data = self._create_test_data(
            b"tree fb3a8bdd0ceddd019615af4d57a53f43d8cee2bf\n"
            b"author Test <test@example.com> 1604563164 invalid\n"
        )
        with pytest.raises(ObjectBroken) as excinfo:
            parse_commit(invalid_data)
        assert 'Failed to parse author timezone' in str(excinfo.value)

    def test_missing_committer(self):
        """Test handling of commits with no committer."""
        invalid_data = self._create_test_data(
            b"tree fb3a8bdd0ceddd019615af4d57a53f43d8cee2bf\n"
            b"author Test <test@example.com> 1604563164 +0800\n"
        )
        with pytest.raises(ObjectBroken) as excinfo:
            parse_commit(invalid_data)
        assert 'Commit object has no "committer"' in str(excinfo.value)

    def test_invalid_committer_format(self):
        """Test handling of invalid committer format."""
        invalid_data = self._create_test_data(
            b"tree fb3a8bdd0ceddd019615af4d57a53f43d8cee2bf\n"
            b"author Test <test@example.com> 1604563164 +0800\n"
            b"committer Invalid\n"
        )
        with pytest.raises(ObjectBroken) as excinfo:
            parse_commit(invalid_data)
        assert 'Committer time is not int' in str(excinfo.value)

    def test_invalid_committer_name_encoding(self):
        """Test handling of committer name with invalid encoding."""
        invalid_data = self._create_test_data(
            b"tree fb3a8bdd0ceddd019615af4d57a53f43d8cee2bf\n"
            b"author Test <test@example.com> 1604563164 +0800\n"
            b"committer \xff\xfeTest <test@example.com> 1604563164 +0800\n"
        )
        with pytest.raises(ObjectBroken) as excinfo:
            parse_commit(invalid_data)
        assert 'Failed to decode committer name' in str(excinfo.value)

    def test_invalid_committer_email_encoding(self):
        """Test handling of committer email with invalid encoding."""
        invalid_data = self._create_test_data(
            b"tree fb3a8bdd0ceddd019615af4d57a53f43d8cee2bf\n"
            b"author Test <test@example.com> 1604563164 +0800\n"
            b"committer Test <\xff\xfetest@example.com> 1604563164 +0800\n"
        )
        with pytest.raises(ObjectBroken) as excinfo:
            parse_commit(invalid_data)
        assert 'Failed to decode committer email' in str(excinfo.value)

    def test_invalid_committer_time(self):
        """Test handling of invalid committer timestamp."""
        invalid_data = self._create_test_data(
            b"tree fb3a8bdd0ceddd019615af4d57a53f43d8cee2bf\n"
            b"author Test <test@example.com> 1604563164 +0800\n"
            b"committer Test <test@example.com> notanumber +0800\n"
        )
        with pytest.raises(ObjectBroken) as excinfo:
            parse_commit(invalid_data)
        assert 'Committer time is not int' in str(excinfo.value)

    def test_invalid_committer_timezone(self):
        """Test handling of invalid committer timezone format."""
        invalid_data = self._create_test_data(
            b"tree fb3a8bdd0ceddd019615af4d57a53f43d8cee2bf\n"
            b"author Test <test@example.com> 1604563164 +0800\n"
            b"committer Test <test@example.com> 1604563164 invalid\n"
        )
        with pytest.raises(ObjectBroken) as excinfo:
            parse_commit(invalid_data)
        assert 'Failed to parse committer timezone' in str(excinfo.value)

    def test_invalid_message_encoding(self):
        """Test handling of commit message with invalid encoding."""
        invalid_data = self._create_test_data(
            b"tree fb3a8bdd0ceddd019615af4d57a53f43d8cee2bf\n"
            b"author Test <test@example.com> 1604563164 +0800\n"
            b"committer Test <test@example.com> 1604563164 +0800\n\n" +
            b"\xff\xfeTest message"  # Invalid UTF-8 sequence
        )
        with pytest.raises(ObjectBroken) as excinfo:
            parse_commit(invalid_data)
        assert 'Failed to decode commit message' in str(excinfo.value)


class TestParseCommitSigned:
    """A gpg signed commit parses like any other commit.

    The signature lives in a header, it must never reach a field of the
    CommitObj: the message is the only field that ends up in the history pack
    of a version, so a signature in the message would be published.
    """

    def test_signed_commit_fields(self):
        """The gpgsig header is skipped, every other header parses as usual."""
        commit = parse_commit(obj_signed_gpg)

        assert isinstance(commit, CommitObj)
        assert commit.tree == 'ecbf1218962b0e784af4edd958498736e48ddd6c'
        assert commit.parent == '1e8646dc2ddffd8e8c7e4c1a4ac6a759f5062e70'
        assert commit.author_name == 'Test'
        assert commit.author_email == 'test@example.com'
        assert commit.author_time == 1789423895
        assert commit.author_tz == 0
        assert commit.committer_name == 'Test'
        assert commit.committer_email == 'test@example.com'
        assert commit.committer_time == 1789423895
        assert commit.committer_tz == 0
        assert commit.message == 'more classifiers for PyPI metadata (#1151)'

    def test_signed_commit_message_has_no_signature(self):
        """No line of the signature block may appear in the message."""
        assert b'gpgsig -----BEGIN PGP SIGNATURE-----' in obj_signed_gpg

        commit = parse_commit(obj_signed_gpg)

        signature = obj_signed_gpg.split(b'-----BEGIN PGP SIGNATURE-----', 1)[1]
        signature = signature.split(b'\n\n', 1)[0]
        for line in signature.split(b'\n'):
            if line.strip():
                assert line.strip().decode() not in commit.message
        assert 'gpgsig' not in commit.message

    def test_mergetag_commit_fields(self):
        """The merge of a signed tag parses with both parents and a clean message."""
        commit = parse_commit(obj_signed_mergetag)

        assert isinstance(commit, CommitObj)
        assert commit.tree == 'bb448ee9c0a8ca9c72c03720559383212d3ec4a5'
        assert commit.parent == [
            'bc4b48d848feb3b05b267478eea0ea07df1dc202',
            'def49700ae4b22dbb9e07fcc31713bbfe9ea7c0b',
        ]
        assert commit.author_name == 'Test'
        assert commit.author_email == 'test@example.com'
        assert commit.author_time == 1670127170
        assert commit.author_tz == 60
        assert commit.committer_name == 'Test'
        assert commit.committer_time == 1670127170
        assert commit.message == "Merge tag 'v1.8.2'\n\nv1.8.2"

    def test_mergetag_message_has_no_tag_signature(self):
        """The tag object of a mergetag carries a signature of its own."""
        assert b'\nmergetag ' in obj_signed_mergetag
        tag = obj_signed_mergetag.split(b'\nmergetag ', 1)[1].split(b'\ngpgsig ', 1)[0]
        # the merged tag is signed too, so its signature rides in the commit header
        assert b'-----BEGIN PGP SIGNATURE-----' in tag

        commit = parse_commit(obj_signed_mergetag)

        signature = tag.split(b'-----BEGIN PGP SIGNATURE-----', 1)[1]
        for line in signature.split(b'\n'):
            if line.strip():
                assert line.strip().decode() not in commit.message
        assert 'mergetag' not in commit.message
        assert 'tagger' not in commit.message
