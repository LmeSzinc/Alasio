"""
Parse a client requirements file into the distributions pinned to an exact
version, and the index each pin came from.

The environment of a client has to satisfy the requirements file of the
deployed tree, the file the mod entry names (ModEntryInfo.requirements_file,
e.g. 'requirements.txt'): it lists the distributions the project runs with,
every one of them pinned to the exact version of the environment. This module
parses one such file into its pins, so the client can read the file without
pip: the pins are what a dependency check compares with the installed
distributions (PipList), the versions the environment has to satisfy.

The parser takes the content of the file as a string -- the caller reads the
file, so the parser touches no disk -- and provides the pins as dict_pins: a
dict of the PEP 503 normalized distribution name to a DepInfo, e.g.
{'httpx': DepInfo('httpx', '0.28.1', '')}. dict_deps is the version view of
the same pins, {'httpx': '0.28.1'}, and dict_sources the source view. The pins
are parsed from the content on the first read of dict_pins and cached on the
parser.

A declaration that does not pin a version is skipped: a range (>=, <=, ~=,
!=), a wildcard (==1.2.*), a bare name, a URL, a local path and a pip option
have no version to install. The extras and the environment markers of a pin
are read as they are, no marker is evaluated: the parser does not resolve
dependencies, it reads the versions a file declares.

An exact pin is one version: when two different versions of one name appear
in one file, the first one is kept and the later ones are warnings, so the
parse reads as much of the file as it can.

The index of a pin is the '# from <url>' annotation of the compiled
requirements file, the annotation uv and pip-compile write with
--emit-index-annotation. Both annotation styles are read: the line style
('    # via httpx, starlette') and the split style ('    # via' followed by
'    #   httpx'), with the annotation on the line after the pin. The
'# via' annotations and the other comments are ignored.

A pin without a '# from' annotation has no source; the official PyPI index
(https://pypi.org/simple) is no source either, the download side probes the
mirrors of the mod entry. An annotation that does not name an index (a wheel
URL, file://, git+...) is a warning and leaves the source undefined.

The format rules are implemented once, in this module: the client is the side
that ships, the release tooling imports it, never the other way around. The
release side reuses Requirements for a requirements file and parse_requirement
for the PEP 508 texts of a pyproject.toml, see
alasio.deploy_dev.pack_server.parse_dep; the pins of a pyproject.toml are
built by build_pins of that module.

Usage:
    Requirements(content).dict_pins
    Requirements(content).dict_deps
    Requirements(content).dict_sources
"""

import re

from msgspec import Struct

from alasio.deploy.simple_pip.pip_list import normalize_name
from alasio.ext.cache import cached_property
from alasio.logger import logger


class DepInfo(Struct, frozen=True):
    """
    One requirement pinned to an exact version and the index it came from.

    Attributes:
        name (str): PEP 503 normalized distribution name, the dist-key of a
            pack and of the installed distributions of PipList
        version (str): Exact version of the pin
        source (str): Normalized base URL of the index the pin was resolved
            from, '' when the pin has no source: no '# from' annotation, the
            official PyPI, or an annotation that is not an index
    """
    name: str
    version: str
    source: str = ''


def parse_requirement(text):
    """
    Parse one requirement text into a pin.

    The text is a subset of PEP 508: the extras are ignored, an environment
    marker after a ';' is ignored, the version is a text of version
    characters with a digit, a wildcard is not a version. The release side
    parses the PEP 508 texts of a pyproject.toml with it, see
    alasio.deploy_dev.pack_server.parse_dep.

    Args:
        text (str): Requirement text, e.g. 'httpx[cli]==0.28.1; python_version < "3.9"'

    Returns:
        tuple[str, str] | None: (name, version) of the pin, name as written
            in the file, None if the text is not a requirement pinned to an
            exact version
    """
    # the environment marker is not evaluated, the pin is read as it is
    text = text.partition(';')[0].strip()
    match = re.fullmatch(
        # the distribution name, PEP 508, e.g. 'httpx', 'ruamel.yaml'
        r'(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)'
        # the extras, e.g. '[cli]', they are ignored
        r'(?:\s*\[[^\]]*\])?'
        # the exact version operator, e.g. '=='. A range operator of PEP 440
        # ('>=', '~=') and the arbitrary equality ('===') do not match
        r'\s*==\s*'
        # the version: the characters a version is written with, PEP 440 and
        # the legacy forms, e.g. 0.28.1, 1.0rc1, 1!2.0, 1.0+local.1, 1.0-1
        r'(?P<version>[A-Za-z0-9._+!-]+)',
        text,
    )
    if not match:
        return None
    version = match.group('version')
    if not re.search(r'[0-9]', version):
        # a version carries a digit, e.g. 1.0.dev0 does and a text like 'dev'
        # does not: a text without a digit is not a version, no wheel can
        # carry it
        return None
    return match.group('name'), version


def build_dep_infos(deps):
    """
    Build the pins of a dependency file from its parsed DepInfo objects.

    The first occurrence of a pin wins: a later different version or source
    is a warning, the parse continues with the first one. An empty source does
    not overwrite a non-empty one, e.g. a pyproject.toml next to an annotated
    requirements.txt.

    Args:
        deps (Iterable[DepInfo]): Pins, the name already normalized

    Returns:
        dict[str, DepInfo]: PEP 503 normalized name -> pin
    """
    pins = {}
    for dep in deps:
        name = dep.name
        other = pins.get(name)
        if other is None:
            pins[name] = dep
            continue
        if other.version != dep.version:
            logger.warning(
                f'Conflicting versions of "{name}" in the requirements: '
                f'"{other.version}" and "{dep.version}", keeping "{other.version}"')
            continue
        if not other.source and dep.source:
            pins[name] = DepInfo(name, dep.version, dep.source)
            continue
        if dep.source and other.source and dep.source != other.source:
            logger.warning(
                f'Conflicting sources of "{name}" in the requirements: '
                f'"{other.source}" and "{dep.source}", keeping "{other.source}"')
    return pins


# a '# from' annotation names an index, not a distribution file; the path of
# an artifact URL ends with one of the suffixes
_ARTIFACT_SUFFIXES = ('.whl', '.zip', '.tar.gz', '.tgz', '.tar.bz2', '.tar.xz')

# the official index of PyPI: a pin that came from it has no source of its own,
# the download side probes the mirrors of the mod entry
_OFFICIAL_INDEX = 'https://pypi.org/simple'


class Requirements:
    """
    Requirements file of a client, the pip requirement file format.

    dict_pins of the parser is the mapping of the exact pins of the file with
    the index each pin came from, every other declaration is skipped, see the
    module docstring. The forms the parser handles:

    - ``name==version``, with extras (``name[extra]==version``) and an
      environment marker (``name==version; python_version < "3.9"``), a
      marker is not evaluated
    - a line continuation with a trailing ``\\``, e.g. the --hash lines of a
      pip-compile output
    - a comment: a line that starts with '#', or the first inline comment
      after whitespace on a requirement line; the annotations of uv and
      pip-compile are one per line
    - the annotations of uv and pip-compile, in both the line style
      (``# via httpx, starlette``) and the split style (``# via`` followed by
      ``#   httpx``), on the lines after the pin; the ``# from <url>``
      annotation is the index of the pin, the ``# via`` annotations are
      ignored

    The name of a pin is normalized with PEP 503 in dict_pins, the same
    dist-key a pack and the installed distributions of PipList use.

    Usage:
        Requirements(content).dict_pins
        Requirements(content).dict_deps
    """

    def __init__(self, content):
        """
        Args:
            content (str): Content of a requirements file, e.g.
                requirements.txt
        """
        self.content = content
        # normalized source of every '# from' URL: a file has many pins but
        # only a few distinct sources, so the cache hits often
        self._source_cache = {}

    @staticmethod
    def _iter_logical_line(content):
        """
        Iter the logical lines of a requirements file content.

        The UTF-8 BOM, the line endings and the continuations of a trailing
        '\\' are handled here, e.g. the --hash lines of a pip-compile output.
        A line that starts with '#' is one comment and is not split again;
        on a requirement line the first '#' after whitespace starts the inline
        comment. uv and pip-compile write every annotation on its own line.

        Args:
            content (str): Content of a requirements file

        Yields:
            tuple[str, str]: (text, comment) of one logical line, the text
                without the options after the requirement (e.g. --hash), the
                comment without the leading '#' and stripped. A comment line
                yields an empty text.
        """
        # the UTF-8 BOM and the line endings
        content = content.lstrip('\ufeff').replace('\r\n', '\n').replace('\r', '\n')
        # a line that ends with a '\' continues on the next one
        content = re.sub(r'\\[ \t]*\n', '', content)
        for line in content.split('\n'):
            line = line.strip()
            if not line:
                continue
            if line.startswith('#'):
                # a comment line is one comment, it is not split again
                yield '', line[1:].strip()
                continue
            # the first '#' after whitespace starts the inline comment
            match = re.search(r'\s#', line)
            if match:
                text = line[:match.start()].strip()
                comment = line[match.end():].strip()
            else:
                text = line
                comment = ''
            # the options after a requirement, e.g. --hash, are not part of it
            text = re.split(r'\s+(?=-)', text, maxsplit=1)[0]
            yield text, comment

    @staticmethod
    def _build_dep_info(name, version, sources):
        """
        Build the DepInfo of a pin from its '# from' annotations.

        Args:
            name (str): Name as written in the file
            version (str): Exact version of the pin
            sources (list[str]): Distinct normalized sources of the pin

        Returns:
            DepInfo: The pin with its normalized name and source, the first
                source is kept when the pin carries more than one
        """
        if len(sources) > 1:
            logger.warning(
                f'Conflicting sources of "{name}" in the requirements: '
                f'"{sources[0]}" and "{sources[1]}", keeping "{sources[0]}"')
        return DepInfo(
            normalize_name(name), version, sources[0] if sources else '')

    def _read_source(self, comment, name):
        """
        Read the index of one '# from' comment.

        Args:
            comment (str): One comment, without the leading '#', stripped by
                the caller
            name (str): Name of the pin the comment belongs to, for the messages

        Returns:
            str: Normalized base URL of the index, '' when the comment is not a
                '# from' annotation, the annotation is the official PyPI, or
                its text is not an index URL
        """
        # fast path: most comments are '# via' or not annotations at all
        if 'from' not in comment:
            return ''
        match = re.match(r'from\s+(?P<text>\S+)', comment, re.IGNORECASE)
        if not match:
            return ''
        text = match.group('text')
        source = self._source_cache.get(text)
        if source is None:
            # local import: yarl is only needed for a cache miss
            from yarl import URL

            try:
                url = URL(text)
            except (TypeError, ValueError):
                source = ''
            else:
                if url.scheme not in ('http', 'https') or not url.host:
                    source = ''
                else:
                    source = str(URL.build(
                        scheme=url.scheme,
                        authority=url.raw_authority,
                        path=url.raw_path.rstrip('/'),
                        query_string=url.raw_query_string,
                        encoded=True,
                    ))
            self._source_cache[text] = source
        if source == _OFFICIAL_INDEX:
            # an empty source lets the download side probe the mirrors of the
            # mod entry
            return ''
        if not source:
            logger.warning(
                f'The "# from" annotation of "{name}" is not an index url: {text!r}, the source is undefined')
            return ''
        # local import: yarl is only needed for a source that is checked here
        from yarl import URL

        if URL(source).path.lower().endswith(_ARTIFACT_SUFFIXES):
            logger.warning(
                f'The "# from" annotation of "{name}" is not an index url: {text!r}, the source is undefined')
            return ''
        return source

    def _iter_dep_info(self):
        """
        Iter the pins of the file and their sources.

        The '# from' annotation belongs to the pin before it, the '# via'
        annotation and the other comments are ignored. An option line does not
        end a pin, a new requirement line does.

        Yields:
            DepInfo: The pin of a requirement line, in the file order
        """
        name = version = ''
        sources = []

        for text, comment in self._iter_logical_line(self.content):
            if not text:
                # a comment line: '# from' belongs to the pin before it, '# via'
                # and the other comments are ignored
                if name and comment:
                    source = self._read_source(comment, name)
                    if source and source not in sources:
                        sources.append(source)
                continue

            if text.startswith('-'):
                # an option line, e.g. --index-url, --hash or -r, belongs to
                # the file, not to the pin before it
                continue

            # a new requirement line commits the pin before it; a line that
            # does not pin an exact version commits it too and starts no pin
            if name:
                yield self._build_dep_info(name, version, sources)
            name = version = ''
            sources = []

            dep = parse_requirement(text)
            if dep is None:
                continue
            name, version = dep
            if comment:
                source = self._read_source(comment, name)
                if source:
                    sources.append(source)

        if name:
            yield self._build_dep_info(name, version, sources)

    @cached_property
    def extra_index(self):
        """
        URL of the extra index the file declares with --extra-index-url.

        The option is usually in the header of the file: the content is
        scanned line by line from its start and the scan stops at the first
        declaration.

        Returns:
            str: URL of the first --extra-index-url, e.g.
                'https://example.com/simple', '' when the file declares none
        """
        for line in self.content.lstrip('\ufeff').splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            # the two forms: --extra-index-url <url> and --extra-index-url=<url>
            for prefix in ('--extra-index-url ', '--extra-index-url='):
                _, sep, value = line.partition(prefix)
                if sep:
                    value = value.strip()
                    # the url is the first token, an inline comment follows it
                    return value.split(None, 1)[0] if value else ''
        return ''

    @cached_property
    def dict_pins(self):
        """
        Pins of the file and their sources, parsed on the first read and
        cached.

        Returns:
            dict[str, DepInfo]: PEP 503 normalized name -> exact version and
                source of every requirement pinned with '==', e.g.
                {'httpx': DepInfo('httpx', '0.28.1', '')}

        The first version and source of a name win, a later different one is
        a warning.
        """
        pins = build_dep_infos(self._iter_dep_info())
        if self.extra_index and pins and not any(pin.source for pin in pins.values()):
            # one summary warning instead of one per pin: the extra index is
            # declared, but no pin comes from it (the annotations are missing
            # or the pins are all from the official PyPI)
            logger.warning(
                f'{len(pins)} pins have no provided source, treated as official: '
                f'the requirements file declares the extra index "{self.extra_index}"')
        return pins

    @cached_property
    def dict_deps(self):
        """
        Pins of the file, parsed on the first read and cached.

        Returns:
            dict[str, str]: PEP 503 normalized name -> exact version of every
                requirement pinned with '==', e.g. {'httpx': '0.28.1'}

        The first version of a name wins, a later different one is a warning.
        """
        return {name: pin.version for name, pin in self.dict_pins.items()}

    @cached_property
    def dict_sources(self):
        """
        Index of every pin of the file, parsed on the first read and cached.

        Returns:
            dict[str, str]: PEP 503 normalized name -> normalized base URL of
                the index the pin came from, '' for a pin without a '# from'
                annotation
        """
        return {name: pin.source for name, pin in self.dict_pins.items()}
