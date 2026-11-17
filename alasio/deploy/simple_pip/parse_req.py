"""
Parse a client requirements file into the distributions pinned to an exact
version, and the index each pin came from.

The environment of a client has to satisfy the requirements file of the
deployed tree, the file the mod entry names (ModEntry.requirements_file,
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

A declaration that does not pin a version is skipped by dict_pins: a range
(>=, <=, ~=, !=), a wildcard (==1.2.*), a bare name, a URL, a local path and a
pip option have no version to install. The ranges are not lost, though: specs
is the same file as the requirement texts they declare, read by
RequirementSpec.from_line, which keeps the specifier, the extras and the
marker,
and refuses nothing but a text that is not a requirement. A range has no
version to install, so a consumer that pins versions reads dict_pins; a
consumer that resolves versions (the compiler of the developer side) reads
specs. The extras and the environment markers of a pin are read as they are,
no marker is evaluated: the parser does not resolve dependencies, it reads
the versions a file declares.

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
URL, file://, git+...) is a warning and leaves the source undefined. The url
of an index is normalized once, by normalize_index_url(): the release side
compares the sources of the pins with the indexes a mod provides, so the two
sides must agree on what one index is.

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
    Requirements(content).specs
"""

import re
from typing import Tuple

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
    marker after a ';' is ignored, the version is the version of a specifier
    that pins exactly one ('=='), a wildcard is not a version. The release
    side parses the PEP 508 texts of a pyproject.toml with it, see
    alasio.deploy_dev.pack_server.parse_dep; the whole requirement, ranges
    included, is read by RequirementSpec.from_line.

    Args:
        text (str): Requirement text, e.g. 'httpx[cli]==0.28.1; python_version < "3.9"'

    Returns:
        tuple[str, str] | None: (name, version) of the pin, name as written
            in the file, None if the text is not a requirement pinned to an
            exact version
    """
    spec = RequirementSpec.from_line(text)
    if spec is None or spec.url:
        # a url, a git repository or a local path is not a version of an index
        return None
    version = spec.pin_version
    if version is None:
        return None
    return spec.name, version


class RequirementSpec(Struct, frozen=True, dict=True):
    """
    One requirement text of a dependency file, the one model of a constraint.

    The requirement is kept whole rather than as a version: a consumer that
    installs a pinned environment reads the version (parse_requirement), a
    consumer that resolves versions reads the specifier. It is the same value
    for both sides of the project, the client that installs and the compiler
    that resolves, so the two never carry a shape of a requirement of their
    own.

    ``specifier`` is the text as it is written, and ``specifiers`` is its
    parsed form: the text is what a report prints and what a lock writes, and
    the parse is done once and cached on the instance, so a resolver does not
    walk back from a model to a text and parse the text again.

    Attributes:
        name (str): Distribution name, as written in the file, e.g. 'PyYAML'
        extras (Tuple[str, ...]): Extras the requirement asks for, in the
            written order, e.g. ('backend', 'image')
        specifier (str): PEP 440 specifier text, as written, '' when the
            requirement pins nothing, e.g. '>=1.0,<2.0,!=1.4.1'
        marker (str): Environment marker after the ';', as written, '' when
            the requirement has none. It is never evaluated here
        url (str): Url of a direct reference ('name @ https://...'), '' for a
            requirement a version of an index satisfies
        origin (str): Who declares the requirement, an annotation of the
            consumer rather than of the file: the project ('pyproject.toml')
            or the distribution that requires the name, '' when the consumer
            does not track it
        source (str): Base url of the index the requirement names, '' for any
            index, see [tool.uv.sources] of the compiler
    """
    name: str
    extras: Tuple[str, ...] = ()
    specifier: str = ''
    marker: str = ''
    url: str = ''
    origin: str = ''
    source: str = ''

    @cached_property
    def specifiers(self):
        """
        The constraint as a specifier set, parsed on the first read.

        The parse belongs to the requirement: a resolver asks the same
        requirement once per candidate version of its name, and the cached
        set dies with the requirement. A cache of the process would keep the
        constraints of a finished run alive, and the constraints of two runs
        are the versions of two different locks.

        The import is local on purpose: the wheel selector of the deploy side
        brings the http client, trio and sqlite with it, which the client that
        only parses a requirements file must not pay for.

        Returns:
            SpecifierSet: The parsed constraint, empty when the requirement
                pins nothing

        Raises:
            ValueError: If the text is not a PEP 440 specifier. Nothing is
                cached then, so every read raises
        """
        from alasio.deploy.pip_download._specifiers import InvalidSpecifier, SpecifierSet

        if not self.specifier:
            return SpecifierSet()
        try:
            return SpecifierSet(self.specifier)
        except InvalidSpecifier as e:
            raise ValueError(
                f'Invalid specifier "{self.specifier}" of "{self.name}": {e}') from None

    @cached_property
    def pin_version(self):
        """
        The version the requirement pins exactly, read on the first access.

        An exact pin is one '==' specifier whose version is a version: a range,
        a set of specifiers, a wildcard (``==1.2.*``) and the arbitrary
        equality (``===x``) pin nothing a wheel is installed by, so they have
        no version. A consumer that installs a pinned environment reads this
        (the client); a consumer that resolves versions reads ``specifiers``.

        Returns:
            str | None: The version, e.g. '0.28.1' of '==0.28.1', None when the
                requirement is not an exact pin

        Raises:
            ValueError: If the text is not a PEP 440 specifier, see specifiers
        """
        specifiers = self.specifiers
        if len(specifiers) != 1:
            return None
        spec = next(iter(specifiers))
        if spec.operator != '==':
            return None
        version = spec.version
        if version.endswith('.*'):
            return None
        if not re.search(r'[0-9]', version):
            # a version carries a digit, e.g. 1.0.dev0 does and a text like
            # 'dev' does not: a text without a digit is not a version, no
            # wheel can carry it
            return None
        return version

    @classmethod
    def from_line(cls, text):
        """
        Parse one requirement line of a dependency file.

        The requirement is read, not resolved: the specifier is validated by
        the specifier parser itself (:attr:`specifiers`, read once and kept on
        the requirement) and left as written, the extras are validated by the
        rule of PEP 508, and the marker is kept as written. A direct reference
        (``name @ url``) is read too, its url in the field: whether a url is
        usable is the decision of the caller, the client that installs skips it
        and the compiler refuses it.

        The patterns are written in the method rather than at module level: the
        `re` module keeps its own cache of the last patterns it compiled, so a
        module level constant only saves a dict lookup, and a constant of a
        module the client imports at start up stays in memory for the life of
        the process.

        Args:
            text (str): Requirement text, e.g.
                'httpx[cli]>=0.28,<0.29; python_version < "3.9"'

        Returns:
            RequirementSpec | None: The requirement, None when the text is not
                a PEP 508 requirement (a pip option, a comment that reached
                here, a malformed specifier)
        """
        # the name of a distribution, PEP 508: an alphanumeric at each end, and
        # the separators of PEP 503 in between
        name = r'[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?'
        # the requirement text: the name, the extras, then either a direct
        # reference ('@ url') or a PEP 440 specifier, then the environment
        # marker. The specifier is written as it is or in the parentheses of
        # the legacy form, 'name (<3.0,>=2.0.6)'. The marker cannot carry a
        # ';' (PEP 508), so the last part of the text is the marker
        match = re.match(
            r'^\s*(?P<name>' + name + r')'
            r'\s*(?:\[(?P<extras>[^\]]*)\])?'
            r'\s*(?:@\s*(?P<url>[^;]+)'
            r'|\((?P<paren>[^;)]*)\)'
            r'|(?P<specifier>[^;@()]*))'
            r'\s*(?:;\s*(?P<marker>.*?))?\s*$',
            text,
        )
        if not match:
            return None

        extras = []
        for extra in (match.group('extras') or '').split(','):
            extra = extra.strip()
            if not extra:
                continue
            if not re.match(name + r'$', extra):
                return None
            extras.append(extra)

        url = (match.group('url') or '').strip()
        marker = (match.group('marker') or '').strip()
        if url:
            return cls(
                name=match.group('name'), extras=tuple(extras), marker=marker, url=url)

        text = match.group('paren') if match.group('paren') is not None else match.group('specifier')
        spec = cls(
            name=match.group('name'), extras=tuple(extras),
            specifier=(text or '').strip(), marker=marker)
        try:
            # the specifier parser is the one authority on the PEP 440 rules,
            # and its answer is kept on the requirement: the consumer that
            # evaluates the constraint reads it from there, it does not parse
            # the text again
            _ = spec.specifiers
        except ValueError:
            return None
        return spec


def normalize_index_url(url):
    """
    Normalize the base url of a PyPI simple index (PEP 503).

    The rule is shared by the side that reads a dependency file and the side
    that compares its sources with the indexes a mod provides (e.g. the pack
    server): the scheme and the host are lowercased, the default port and the
    trailing slash are dropped, so 'https://PyPI.org:443/simple/' and
    'https://pypi.org/simple' are one index.

    Args:
        url (str): Url as written, e.g. 'https://py38deps.github.io/py38deps/simple/'

    Returns:
        str: Normalized base url, '' when the text is not an http(s) url or
            carries no host
    """
    # local import: yarl is only needed by the parse of a '# from' comment
    from yarl import URL

    try:
        parsed = URL(url)
    except (TypeError, ValueError):
        return ''
    if parsed.scheme not in ('http', 'https') or not parsed.host:
        return ''
    return str(URL.build(
        scheme=parsed.scheme,
        authority=parsed.raw_authority,
        path=parsed.raw_path.rstrip('/'),
        query_string=parsed.raw_query_string,
        encoded=True,
    ))


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
        # the final result of every '# from' comment: a file repeats the same
        # annotation line for every pin of an index, so the cache hits often
        self._source_cache = {}

    @staticmethod
    def _iter_logical_line(content):
        """
        Iter the logical lines of a requirements file content.

        The UTF-8 BOM, the line endings and the continuations of a trailing
        '\\' are handled here, e.g. the --hash lines of a pip-compile output.
        A line that starts with '#' is one comment and is not split again;
        on a requirement line the first '#' after whitespace starts the inline
        comment. Only a comment that carries 'from' is yielded, the via lines
        and the other comments are skipped. uv and pip-compile write every
        annotation on its own line.

        Args:
            content (str): Content of a requirements file

        Yields:
            tuple[str, str]: (text, comment) of one logical line, the text
                without the options after the requirement (e.g. --hash), the
                comment without the leading '#' and stripped. A comment line
                yields an empty text.
        """
        # the UTF-8 BOM and the line endings
        content = content.lstrip('\ufeff')
        if '\r' in content:
            content = content.replace('\r\n', '\n').replace('\r', '\n')
        # a line that ends with a '\' continues on the next one
        if '\\' in content:
            content = re.sub(r'\\[ \t]*\n', '', content)
        for line in content.split('\n'):
            line = line.strip()
            if not line:
                continue
            if line.startswith('#'):
                # only a '# from' comment matters, the via lines and the
                # other comments are skipped here
                comment = line[1:].strip()
                if 'from' in comment:
                    yield '', comment
                continue
            # the first '#' after whitespace starts the inline comment
            text, sep, comment = line.partition('#')
            if sep and text and text[-1].isspace():
                text = text.strip()
                comment = comment.strip()
                if 'from' not in comment:
                    comment = ''
            else:
                text = line
                comment = ''
            # the options after a requirement, e.g. --hash, are not part of it
            if '--' in text:
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
        cached = self._source_cache.get(comment)
        if cached is not None:
            source, text, error = cached
        else:
            match = re.match(r'from\s+(?P<text>\S+)', comment, re.IGNORECASE)
            if not match:
                return ''
            text = match.group('text')
            error = False
            source = normalize_index_url(text)
            if not source:
                error = True
            elif source == _OFFICIAL_INDEX:
                # an empty source lets the download side probe the mirrors of
                # the mod entry
                source = ''
            elif source.partition('?')[0].lower().rstrip('/').endswith(_ARTIFACT_SUFFIXES):
                source = ''
                error = True
            self._source_cache[comment] = (source, text, error)
        if error:
            logger.warning(
                f'The "# from" annotation of "{name}" is not an index url: {text!r}, the source is undefined')
        return source

    @cached_property
    def _records(self):
        """
        Every logical line of the file, its requirement parsed once.

        The file is read twice by its consumers (the pins of dict_pins and the
        requirements of specs), and both need the same parse of the same line:
        this is the parse, read once and kept on the parser, so a requirement
        line of a file is read into a :class:`RequirementSpec` once however
        many views of the file are asked for.

        Returns:
            list[tuple[RequirementSpec | None, str, bool]]: For every logical
                line, (the requirement, the comment, whether the line declares
                a requirement). A comment line declares nothing, its
                requirement is None and its comment is the text after the '#';
                an option line declares nothing either, and carries no text
        """
        return [
            (
                RequirementSpec.from_line(text) if text and not text.startswith('-') else None,
                comment,
                bool(text) and not text.startswith('-'),
            )
            for text, comment in self._iter_logical_line(self.content)
        ]

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

        for spec, comment, is_line in self._records:
            if not is_line:
                # a comment line: '# from' belongs to the pin before it, '# via'
                # and the other comments are ignored
                if name and comment:
                    source = self._read_source(comment, name)
                    if source and source not in sources:
                        sources.append(source)
                continue

            # a new requirement line commits the pin before it; a line that
            # does not pin an exact version commits it too and starts no pin
            if name:
                yield self._build_dep_info(name, version, sources)
            name = version = ''
            sources = []

            if spec is None:
                continue
            version = spec.pin_version
            if version is None:
                continue
            name = spec.name
            if comment:
                source = self._read_source(comment, name)
                if source:
                    sources.append(source)

        if name:
            yield self._build_dep_info(name, version, sources)

    @cached_property
    def specs(self):
        """
        The requirements the file declares, ranges and markers included.

        The lines are the ones dict_pins reads (an option line and an inline
        '# from' annotation included in the file format), but a line that
        declares a range is kept rather than skipped: a caller that resolves
        the versions of a file reads them here.

        Returns:
            list[RequirementSpec]: The requirements, in the file order, e.g.
                [RequirementSpec('numpy', specifier='<2')]
        """
        return [spec for spec, _comment, _line in self._records if spec is not None]

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
