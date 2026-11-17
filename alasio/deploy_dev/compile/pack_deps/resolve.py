"""
Decide whether a pinned distribution can be served by the pack channel of a mod.

A dependency of a mod is installed from the pack channel when the mod declares
it in ``Update.pack_deps`` (see alasio.config.entry.mod_entry); every other
distribution is installed with pip. The pack channel builds one pack per
version from the one wheel of that version that is pure python
(``abi=none``, ``platform=any``), see
alasio.deploy_dev.pack_server.repo_dep, so the decision is a property of the
wheels a version publishes, not of the source of the project:

- a version that publishes a pure wheel and no compiled wheel a client can use
  is ``pack``: the pack channel serves exactly the file pip would install;
- a version that publishes a compiled wheel the target interpreter can install
  is ``pip``: the pack channel would replace that build with the pure fallback
  on every platform, which changes what the mod runs on, not only how the
  distribution is updated;
- a version that publishes no pure wheel is ``pip``: the pack channel cannot
  build a pack of it at all.

The compiled wheels are matched by tag only, the platform is ignored (see
:func:`tag_covers_target`): the packs of a mod are served to windows, linux and
macOS clients, and a build that exists for one of them is enough to keep the
distribution on pip. The interpreter is the one that runs the tool, see
:class:`alasio.deploy.pip_download._target_python.TargetPython`.

The answer is conservative on purpose. A distribution that is wrongly left on
pip still installs, it only loses the update path of the pack channel; a
distribution that is wrongly declared in ``pack_deps`` fails the run of the
pack server (a version without a pure wheel is not a pack target) and the
dependency step of every client (the packed tree records no snapshot of it).
So anything the caller cannot decide becomes ``pip``, with the reason kept in
the decision.

Usage:
    from alasio.deploy_dev.compile.pack_deps.resolve import decide, wheels_of

    candidates = wheels_of(page.links, 'httpx2', '2.12.0')
    decision = decide('httpx2', '2.12.0', page.url, candidates, target)
    decision.decision     # 'pack'
    decision.reason       # 'pure-only'
"""

import re
from typing import List

from msgspec import Struct, field

from alasio.deploy.pip_download._specifiers import InvalidSpecifier
from alasio.deploy.pip_download._utils import check_requires_python
from alasio.deploy.pip_download.pypi_file import DOWNLOAD_MODE_ALL, select_wheels
from alasio.logger import logger

__all__ = [
    'DECISION_PACK',
    'DECISION_PIP',
    'REASON_COMPILED',
    'REASON_FORCED',
    'REASON_INDEX',
    'REASON_NOT_INSTALLABLE',
    'REASON_NO_PURE',
    'REASON_NO_WHEEL',
    'REASON_PURE_ONLY',
    'PackDepDecision',
    'decide',
    'tag_covers_target',
    'wheel_compiled_for_target',
    'wheel_pure_for_target',
    'wheels_of',
]

# The two decisions of a distribution: updated by the pack channel, or left to
# pip by the caller
DECISION_PACK = 'pack'
DECISION_PIP = 'pip'

# Why a distribution was decided, kept on the decision for the report
# a pure wheel and no compiled wheel the target can install
REASON_PURE_ONLY = 'pure-only'
# a pure wheel, and a compiled wheel the target can install
REASON_COMPILED = 'compiled-wheel'
# wheels, but none of them is pure python
REASON_NO_PURE = 'no-pure-wheel'
# no wheel of the version at all, the pinned version is not published
REASON_NO_WHEEL = 'no-wheel'
# wheels, but none of them installs on the target interpreter (yanked ones are
# already dropped, a Requires-Python of the file included)
REASON_NOT_INSTALLABLE = 'not-installable'
# the index page of the distribution cannot be read
REASON_INDEX = 'index-error'
# declared by register(..., force=True), the index is not read
REASON_FORCED = 'forced'

# The interpreter tag of a CPython build, e.g. 'cp38' of 'cp38-cp38-win_amd64'
_CP_INTERPRETER = re.compile(r'cp(\d)(\d+)')
# The interpreter tag of a python build of one version, e.g. 'py38'
_PY_INTERPRETER = re.compile(r'py(\d)(\d+)')
# The interpreter tag of every python of one major version, e.g. 'py3'
_PY_MAJOR = re.compile(r'py(\d)')


class PackDepDecision(Struct):
    """
    What one distribution of the closure asks for, and the wheels it was read
    from.

    Attributes:
        name (str): PEP 503 normalized distribution name
        version (str): Version the requirements file pins
        decision (str): DECISION_PACK or DECISION_PIP
        reason (str): Why, one of the REASON_* constants
        source (str): Url of the index page the wheels were read from, '' when
            the index was not read
        pure_wheel (str): Filename of the pure wheel that makes the version a
            pack target, '' when the version has none
        compiled_wheels (List[str]): Filenames of the compiled wheels the
            target interpreter can install, the evidence of a ``pip`` decision
    """

    name: str
    version: str
    decision: str
    reason: str
    source: str = ''
    pure_wheel: str = ''
    compiled_wheels: List[str] = field(default_factory=list)

    @property
    def pack(self):
        """
        Returns:
            bool: True when the distribution updates from the pack channel
        """
        return self.decision == DECISION_PACK


def tag_covers_target(tag, target):
    """
    Whether one wheel tag is installable by the target CPython.

    The platform of the tag is not read here: the caller decides whether it
    wants a platform-specific build, see wheel_compiled_for_target(). The
    interpreter and the ABI relation is the rule of the wheel format:

    - ``py3`` covers every python 3, ``py38`` covers python 3.8 and older;
    - ``cp38`` names one CPython minor version, a build is installable on that
      version only (the ABI is not stable across minor versions);
    - ``abi3`` is the stable ABI of the version it names and every newer one;
    - any other ABI (``none`` included) belongs to the interpreter tag it is
      written with.

    Args:
        tag (Tag): Tag of a wheel, from Wheel.file_tags
        target (TargetPython): The interpreter to check against

    Returns:
        bool: True when the tag installs on the target
    """
    version = tuple(target.py_version_info[:2])
    interpreter = tag.interpreter
    abi = tag.abi

    major = _PY_MAJOR.fullmatch(interpreter)
    if major:
        return abi == 'none' and int(major.group(1)) == version[0]

    python = _PY_INTERPRETER.fullmatch(interpreter)
    if python:
        return abi == 'none' and (int(python.group(1)), int(python.group(2))) <= version

    cpython = _CP_INTERPRETER.fullmatch(interpreter)
    if not cpython:
        # another implementation (pypy, graalpy, ...) or a tag of a format this
        # reader does not know: it is never a wheel of the target
        return False

    build = (int(cpython.group(1)), int(cpython.group(2)))
    if abi == 'abi3':
        return build <= version
    if abi == 'none':
        # a pure wheel of one python version, limited to a platform
        return build == version
    # a build ABI is exact: cp38-cp38, cp313-cp313t
    return abi == interpreter and build == version


def wheel_pure_for_target(wheel, target):
    """
    Whether the wheel is a pure python build the target can install.

    The pack channel builds a pack from the pure wheel of a version only
    (alasio.deploy.pip_download._wheel.is_pure_wheel), so this is what makes a
    version a pack target. The interpreter is checked against the target too: a
    ``py313-none-any`` wheel is pure, and useless to a python 3.8 client.

    Args:
        wheel (Wheel): The wheel to check
        target (TargetPython): The interpreter to check against

    Returns:
        bool: True for a pure python wheel of the target
    """
    return any(
        tag.abi == 'none' and tag.platform == 'any'
        and tag.interpreter.startswith('py3') and tag_covers_target(tag, target)
        for tag in wheel.file_tags
    )


def wheel_compiled_for_target(wheel, target):
    """
    Whether the wheel is a platform build the target can install.

    Args:
        wheel (Wheel): The wheel to check
        target (TargetPython): The interpreter to check against

    Returns:
        bool: True when the file is limited to a platform and installs on the
            target
    """
    return any(
        tag.platform != 'any' and tag_covers_target(tag, target)
        for tag in wheel.file_tags
    )


def wheels_of(links, name, version):
    """
    The wheel candidates of one version of a distribution.

    The yanked files are dropped, unless every candidate is yanked: PEP 592
    lets a client install a yanked file when the pinned version has nothing
    else, which is the rule of select_wheels() too.

    Args:
        links (Iterable[Link]): Links of the index page of the distribution
        name (str): Distribution name, any case and separator style
        version (str): Exact version of the distribution

    Returns:
        list[WheelCandidate]: The wheels of the version

    Raises:
        WheelNotFoundError: If the page lists no wheel of the version
    """
    candidates = select_wheels(links, name, version, DOWNLOAD_MODE_ALL)
    kept = [candidate for candidate in candidates if not candidate.link.is_yanked]
    return kept or candidates


def _requires_python_ok(link, version_info):
    """
    Whether the target interpreter satisfies the `Requires-Python` of a link.

    A link without the attribute is fine, and a value that does not parse is
    taken as fine too: a broken specifier of an index must not hide a file from
    every client, the answer pip gives.

    Args:
        link (Link): Link of the index page
        version_info (tuple[int, ...]): Python version of the target

    Returns:
        bool: True when the file allows the interpreter
    """
    if not link.requires_python:
        return True
    try:
        return check_requires_python(link.requires_python, version_info)
    except InvalidSpecifier as e:
        logger.debug(
            f'Ignoring invalid Requires-Python of "{link.show_url}": '
            f'{link.requires_python!r}, {e}')
        return True


def decide(name, version, source, candidates, target):
    """
    Decide how one pinned version of a distribution is installed.

    Args:
        name (str): PEP 503 normalized name of the distribution
        version (str): Version the requirements file pins
        source (str): Url of the index page the candidates were read from, used
            in the report only
        candidates (Iterable[WheelCandidate]): The wheels of the version, see
            wheels_of()
        target (TargetPython): The interpreter the decision is made for

    Returns:
        PackDepDecision: The decision and the wheels it was read from
    """
    usable = [
        candidate for candidate in candidates
        if _requires_python_ok(candidate.link, target.py_version_info)
    ]
    pure = next(
        (candidate for candidate in usable if wheel_pure_for_target(candidate.wheel, target)),
        None)
    compiled = [
        candidate for candidate in usable if wheel_compiled_for_target(candidate.wheel, target)
    ]

    if pure is not None and not compiled:
        decision, reason = DECISION_PACK, REASON_PURE_ONLY
    elif pure is not None:
        decision, reason = DECISION_PIP, REASON_COMPILED
    elif compiled:
        # the target can install the version, the pack channel cannot build it
        decision, reason = DECISION_PIP, REASON_NO_PURE
    else:
        # no wheel of the version is installable on the target
        decision, reason = DECISION_PIP, REASON_NOT_INSTALLABLE
        declared = sorted({candidate.link.requires_python or 'none' for candidate in candidates})
        logger.warning(
            f'No wheel of "{name}=={version}" installs on python {target.py_version}, '
            f'the lock pins a version this interpreter cannot run: Requires-Python '
            f'{", ".join(declared)}')

    return PackDepDecision(
        name=name,
        version=version,
        decision=decision,
        reason=reason,
        source=source,
        pure_wheel=pure.filename if pure is not None else '',
        compiled_wheels=[candidate.filename for candidate in compiled],
    )


def decision_text(decision):
    """
    One line of the report of a decision.

    Args:
        decision (PackDepDecision): The decision

    Returns:
        str: e.g. 'httpx2==2.12.0 -> pack (pure-only): httpx2-2.12.0-py3-none-any.whl'
    """
    text = f'{decision.name}=={decision.version} -> {decision.decision} ({decision.reason})'
    if decision.compiled_wheels:
        files = ', '.join(decision.compiled_wheels)
        text = f'{text}: {files}'
    elif decision.pure_wheel:
        text = f'{text}: {decision.pure_wheel}'
    if decision.source:
        text = f'{text}, index="{decision.source}"'
    return text
