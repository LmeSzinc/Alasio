from alasio.ext.codegen import CodeGen
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

        # suffix
        value = [suffix for suffix, info in self.iter_suffix_pattern() if info.is_binary]
        with gen.Set('SET_BINARY_SUFFIX'):
            for line in gen.merge_items(value):
                gen.add(line, line_ending=False)
        value = [suffix for suffix, info in self.iter_suffix_pattern() if info.is_text]
        with gen.Set('SET_TEXT_SUFFIX'):
            for line in gen.merge_items(value):
                gen.add(line, line_ending=False)
        value = [suffix for suffix, info in self.iter_suffix_pattern() if info.eol_lf]
        with gen.Set('SET_LF_SUFFIX'):
            for line in gen.merge_items(value):
                gen.add(line, line_ending=False)
        value = [suffix for suffix, info in self.iter_suffix_pattern() if info.eol_crlf]
        with gen.Set('SET_CRLF_SUFFIX'):
            for line in gen.merge_items(value):
                gen.add(line, line_ending=False)

        # name
        value = [suffix for suffix, info in self.iter_name_pattern() if info.is_binary]
        with gen.Set('SET_BINARY_NAME'):
            for line in gen.merge_items(value):
                gen.add(line, line_ending=False)
        value = [suffix for suffix, info in self.iter_name_pattern() if info.is_text]
        with gen.Set('SET_TEXT_NAME'):
            for line in gen.merge_items(value):
                gen.add(line, line_ending=False)
        value = [suffix for suffix, info in self.iter_name_pattern() if info.eol_lf]
        with gen.Set('SET_LF_NAME'):
            for line in gen.merge_items(value):
                gen.add(line, line_ending=False)
        value = [suffix for suffix, info in self.iter_name_pattern() if info.eol_crlf]
        with gen.Set('SET_CRLF_NAME'):
            for line in gen.merge_items(value):
                gen.add(line, line_ending=False)

        gen.write(self.output)


if __name__ == '__main__':
    self = EolConstGen()
    self.read()
    self.gen()
