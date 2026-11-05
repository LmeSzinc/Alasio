"""
Tests of the entry published on the class of a cross-nav generator.

A postprocess function is called with the class resolved from its __qualname__
rather than with the instance, see
alasio.config_dev.parse.postprocess.run_postprocess, so a postprocess reads the
entry of its generator from the class: CrossNavGenerator publishes it there when
it is built.

The entry is published on the class of the instance, not on CrossNavGenerator
itself: a process runs several generators (the generator of the library is built
while a mod is generated), and a shared class attribute would answer the entry of
the last one built.
"""

from alasio.config_dev.gen.gen_cross import CrossNavGenerator
from alasio.config_dev.parse.postprocess import PostprocessInfo, run_postprocess
from alasio.testing.filesystem import fs  # noqa: F401
from tests.config_dev.gen.helpers import make_entry


class SubGeneratorA(CrossNavGenerator):
    def process(cls, data):
        """A postprocess of a generator, it reads the entry from the class."""
        return list(cls.entry.gui_language)


class SubGeneratorB(CrossNavGenerator):
    pass


class TestEntryOnClass:
    def test_the_generator_publishes_its_entry_on_its_class(self, fs):
        entry = make_entry(fs, name='mod_a')
        gen = SubGeneratorA(entry)
        assert gen.entry is entry
        assert SubGeneratorA.entry is entry

    def test_each_generator_class_keeps_its_own_entry(self, fs):
        """The generators of two mods do not overwrite each other."""
        entry_a = make_entry(fs, name='mod_a')
        entry_b = make_entry(fs, name='mod_b')
        SubGeneratorA(entry_a)
        SubGeneratorB(entry_b)
        assert SubGeneratorA.entry is entry_a
        assert SubGeneratorB.entry is entry_b

    def test_a_postprocess_reads_the_entry_from_the_class(self, fs):
        entry = make_entry(fs, name='mod_a', languages=('zh-CN', 'ja-JP'))
        SubGeneratorA(entry)

        info = PostprocessInfo.from_function(SubGeneratorA.process)
        assert info.plan == ('cls_data', '')
        assert run_postprocess(info, {}) == ['zh-CN', 'ja-JP']
