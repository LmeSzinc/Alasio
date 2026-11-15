"""
Maintain the pack_deps of a mod entry, the distributions the mod updates from
its pack channel instead of pip.

The tool reads the requirements file of the mod (the lock), walks the closure
of the distributions the caller registers, decides every version from the
wheels its index publishes, and replaces ``Update.pack_deps`` of
``module/config/_index/mod_entry.yaml`` with the names that are pack targets.

A version is a pack target when it publishes a pure python wheel and no
compiled wheel the target interpreter can install: the pack channel builds its
packs from that one wheel, so declaring a version that also ships a compiled
build would replace the build a client installs today with the pure fallback on
every platform. Everything else stays on pip, which installs the pin the
requirements file declares.

Usage:
    from alasio.deploy_dev.pack_deps import PackDepsManager

    manager = PackDepsManager()                     # env.PROJECT_ROOT
    manager.register('alasio[backend,image]', recursive=True)
    manager.register('my_private_package', force=True)
    manager.write()

The design of the tool, and why the runtime does not derive the list itself, is
in doc/2026-11-15_pack-deps-manager.md.
"""

from alasio.deploy_dev.pack_deps.manager import PackDepsError, PackDepsManager
from alasio.deploy_dev.pack_deps.resolve import (
    DECISION_PACK, DECISION_PIP, REASON_COMPILED, REASON_FORCED, REASON_INDEX, REASON_NO_PURE, REASON_NO_WHEEL,
    REASON_NOT_INSTALLABLE, REASON_PURE_ONLY, PackDepDecision, decide, tag_covers_target, wheel_compiled_for_target,
    wheel_pure_for_target, wheels_of
)

__all__ = [
    'DECISION_PACK',
    'DECISION_PIP',
    'PackDepDecision',
    'PackDepsError',
    'PackDepsManager',
    'REASON_COMPILED',
    'REASON_FORCED',
    'REASON_INDEX',
    'REASON_NOT_INSTALLABLE',
    'REASON_NO_PURE',
    'REASON_NO_WHEEL',
    'REASON_PURE_ONLY',
    'decide',
    'tag_covers_target',
    'wheel_compiled_for_target',
    'wheel_pure_for_target',
    'wheels_of',
]
