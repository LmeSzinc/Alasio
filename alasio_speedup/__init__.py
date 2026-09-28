"""
The C accelerators of alasio, packaged on their own so that they can be
installed, versioned and shipped without the framework.

Every accelerator is one module of the package, named after the area it
speeds up, with its C source next to it:

    bit2.py + bit2_encode.c     the encoder of the bit2 format

Adding an accelerator is adding the pair and the name below:

1. ``<area>.py``: one ``AcceleratorLibrary`` of the package (the stem of the
   C source and the interface version it speaks), a ``check()`` that loads
   the library and encodes a tiny input, and the wrappers of the exports,
   see bit2.py
2. ``<area>_<what>.c``: export ``abi_version()`` plus the entry points of
   the accelerator, see bit2_encode.c. ``python -m alasio_speedup.build``
   builds every C source of the package
3. add ``<area>`` to ACCELERATORS below. ``alasio.speedup`` imports and
   checks the accelerators one by one, so one that cannot build or load does
   not take the others down and every caller falls back to the pure Python
   implementation on its own

The module is optional, nothing here may import alasio.
"""

# The accelerators of the package, one module and one shared library each.
ACCELERATORS = ('bit2',)
