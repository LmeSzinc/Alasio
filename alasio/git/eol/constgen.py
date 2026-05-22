from alasio.codegen.python import CodeGen
from alasio.ext.path import PathStr
from alasio.ext.path.iter import iter_files
from alasio.git.eol.eol import AttrInfo, GitAttribute


class EolConstGen:
    def __init__(self):
        root = PathStr(__file__)
        self.path = root.with_name('gitattributes')
        self.output = root.with_name('const.py')
        # key: pattern, value: AttrInfo object
        self.dict_eol: "dict[str, AttrInfo]" = {}

    def read(self):
        """
        Read all gitattributes
        """
        for file in iter_files(self.path, ext='.gitattributes', recursive=True):
            try:
                reader = GitAttribute(file)
                reader.read()
            except FileNotFoundError:
                continue
            self.dict_eol.update(reader.dict_eol)

    def iter_suffix_pattern(self):
        for patten, info in self.dict_eol.items():
            if not patten.startswith('*.'):
                # not for suffix
                continue
            suffix = patten[2:]
            if '*' in suffix:
                # wildcard
                continue
            if '.' in suffix:
                # multisuffix
                continue
            if '/' in suffix:
                # subpath
                continue
            yield suffix, info

    def iter_name_pattern(self):
        for patten, info in self.dict_eol.items():
            if patten.startswith('*.'):
                # not for suffix
                continue
            if '*' in patten:
                # wildcard
                continue
            if '/' in patten:
                # subpath
                continue
            yield patten, info

    def gen(self):
        gen = CodeGen()
        gen.CommentCodeGen('alasio.git.eol.constgen')
        gen.Empty()

        # suffix
        value = [suffix for suffix, info in self.iter_suffix_pattern() if info.is_binary]
        with gen.Set('SET_BINARY_SUFFIX').wrap():
            for item in value:
                gen.Item(item)

        value = [suffix for suffix, info in self.iter_suffix_pattern() if info.is_text]
        with gen.Set('SET_TEXT_SUFFIX').wrap():
            for item in value:
                gen.Item(item)

        value = [suffix for suffix, info in self.iter_suffix_pattern() if info.eol_lf]
        with gen.Set('SET_LF_SUFFIX').wrap():
            for item in value:
                gen.Item(item)

        value = [suffix for suffix, info in self.iter_suffix_pattern() if info.eol_crlf]
        with gen.Set('SET_CRLF_SUFFIX').wrap():
            for item in value:
                gen.Item(item)

        # name
        value = [suffix for suffix, info in self.iter_name_pattern() if info.is_binary]
        with gen.Set('SET_BINARY_NAME').wrap():
            for item in value:
                gen.Item(item)

        value = [suffix for suffix, info in self.iter_name_pattern() if info.is_text]
        with gen.Set('SET_TEXT_NAME').wrap():
            for item in value:
                gen.Item(item)

        value = [suffix for suffix, info in self.iter_name_pattern() if info.eol_lf]
        with gen.Set('SET_LF_NAME').wrap():
            for item in value:
                gen.Item(item)

        value = [suffix for suffix, info in self.iter_name_pattern() if info.eol_crlf]
        with gen.Set('SET_CRLF_NAME').wrap():
            for item in value:
                gen.Item(item)

        gen.write(self.output)


if __name__ == '__main__':
    self = EolConstGen()
    self.read()
    self.gen()
