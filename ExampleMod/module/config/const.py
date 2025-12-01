from alasio.config.entry.const import ModEntryInfo
from alasio.ext.path import PathStr

entry = ModEntryInfo(name='example_mod')
entry.root = PathStr.new(__file__).uppath(3)
