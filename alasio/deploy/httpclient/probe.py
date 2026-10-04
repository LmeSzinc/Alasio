"""
Concurrent mirror probing: race the candidates of every group and take
the first usable one.

The engine is transport agnostic and knows nothing about the mirror
records: a subclass implements probe_function(), one candidate at a
time, and the engine takes care of the concurrency skeleton. It is
shared by the probes of the deploy domain, e.g. the pack mirrors
(ServerFile probes a mirror by fetching its latest.pack) and the
future pypi mirrors (a probe measures the latency of a mirror with a
HEAD request).

A probe is constructed with its mirror structure and only run()
executes it, so the mirrors of a probe are fixed and visible at
construction. The mirror input is normalized by populate_mirrors().
"""
import re
from queue import Queue
from time import perf_counter

from alasio.ext.concurrent.threadpool import THREAD_POOL
from alasio.logger import logger


class AllMirrorsFailedError(Exception):
    """
    Raised when no candidate of any group is usable.

    The message carries the failure of every candidate, so the caller
    and the logs show what was tried.
    """


def _validate_name(name):
    """
    Check a mirror name against the grammar of the module: an
    alphanumeric start followed by letters, digits, '.', '_' or '-'.

    The name is used in the logs, in the gui.db record and as the
    identity of a mirror (it must be unique across the groups), the
    validation keeps it clean.

    Args:
        name (str): Mirror name to check

    Returns:
        str: The name

    Raises:
        ValueError: If the name is malformed
    """
    # the re cache makes the inline pattern cheap, no global regex
    # needs to live forever
    if not isinstance(name, str) or not re.match(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$', name):
        raise ValueError(f'Invalid mirror name: {name!r}')
    return name


def _validate_url(url):
    """
    Check an url and strip the trailing slashes, the requests append
    '/...' to a base url.

    Args:
        url (str): Url to check

    Returns:
        str: The url without trailing slashes

    Raises:
        ValueError: If the url is not an http/https url
    """
    if not isinstance(url, str):
        raise ValueError(f'Invalid mirror url: {url!r}')
    base = url.rstrip('/')
    scheme, sep, rest = base.partition('://')
    if not sep or scheme not in ('http', 'https') or not rest:
        raise ValueError(f'Invalid mirror url: {url!r}')
    return base


def populate_mirrors(mirrors):
    """
    Normalize a mirror input into the full mirror structure.

    The accepted inputs are described in Mirrors.from_input(). In the
    full structure a group name is a label that tells the groups
    apart, it carries no meaning; the members of a group are tried in
    their declared order (the first usable one wins, a failed member
    falls through to the next), the groups run in parallel. A mirror
    name must be unique across the groups: the selection record stores
    the name.

    Args:
        mirrors (str | dict[str, str] | dict[str, dict[str, str]]):
            The mirror input, a str is one mirror named 'default'

    Returns:
        dict[str, dict[str, str]]: The mirrors as {group: {name: url}},
            the urls without trailing slashes

    Raises:
        TypeError: If the input is neither a str nor a dict, or a
            group value is neither a str nor a dict
        ValueError: If the input is empty, a group is empty, a name is
            malformed or repeats across the groups, or an url is not
            an http/https url
    """
    if isinstance(mirrors, str):
        mirrors = {'default': {'default': mirrors}}
    if not isinstance(mirrors, dict):
        raise TypeError(f'mirrors must be a str or a dict, got {type(mirrors).__name__}')
    if not mirrors:
        raise ValueError('mirrors must not be empty')
    populated = {}
    seen = set()
    for group, members in mirrors.items():
        if not isinstance(group, str):
            raise TypeError(f'Invalid mirror group: {group!r}')
        if isinstance(members, str):
            # the flat form: the key is the mirror name of a group of
            # its own
            members = {group: members}
        elif not isinstance(members, dict):
            raise TypeError(f'mirrors[{group!r}] must be a str or a dict, got {type(members).__name__}')
        if not members:
            raise ValueError(f'Mirror group is empty: {group!r}')
        populated[group] = {
            _validate_name(name): _validate_url(url)
            for name, url in members.items()
        }
        for name in members:
            if name in seen:
                # the record stores the name only, a name must tell a
                # mirror apart across the groups
                raise ValueError(f'Duplicate mirror name: {name!r}')
            seen.add(name)
    return populated


class Mirrors:
    """
    The mirror structure of a probe or an update server.

    groups is the normalized structure {group: {name: url}} (see
    populate_mirrors()), urls is the flat {name: url} map of every
    mirror of every group, the names are unique across the groups.
    """

    def __init__(self, mirrors):
        """
        Args:
            mirrors (str | dict[str, str] | dict[str, dict[str, str]]):
                The mirror input, see populate_mirrors() and
                from_input()
        """
        self.groups = populate_mirrors(mirrors)
        # {name: url} of every mirror of every group, a name is unique
        # across the groups
        self.urls = {name: url for members in self.groups.values() for name, url in members.items()}

    @classmethod
    def from_input(cls, mirrors):
        """
        Create a Mirrors from an input, a Mirrors is returned as-is.

        The accepted forms:

        a single mirror:
            "https://only.example.com/alas"

        mirror name to url, each mirror is a group of its own:
            {
                "cn": "https://123pan.example",
                "global": "https://global.example"
            }

        the flat and the nested form may mix per group:
            {
                "cn": {
                    "123pan": "https://pan",
                    "tencent-cos": "https://cos"
                },
                "global": "https://global.example"
            }

        group to mirrors:
            {
                "cn": {
                    "123pan": "https://123pan.example",
                    "tencent-cos": "https://cos.example",
                    "self-host": "https://self.example"
                },
                "global": {
                    "global": "https://global.example"
                }
            }

        Args:
            mirrors (Mirrors | str | dict): The mirror input

        Returns:
            Mirrors: The mirror structure
        """
        if isinstance(mirrors, cls):
            return mirrors
        return cls(mirrors)

    @property
    def single(self):
        """
        The only mirror name when the structure has one mirror, ''
        otherwise.

        Returns:
            str: The only mirror name, or ''
        """
        if len(self.urls) == 1:
            return next(iter(self.urls))
        return ''

    def url_of(self, name):
        """
        Url of a mirror name.

        Args:
            name (str): Mirror name

        Returns:
            str: The url, '' when the name is not in the structure
        """
        return self.urls.get(name, '')


class ProbeBase:
    """
    Base class of a concurrent candidate probe.

    A candidate is usable when probe_function() returns: the function
    probes one candidate and raises when the candidate is not usable,
    the result of the winner is handed to the caller of run().

    Groups are connectivity paths, the groups run in parallel: the
    earliest group with a usable member wins, the fastest reachable
    path is selected. The members of a group are probed in their
    declared order, the first usable one wins: the order is a
    preference (e.g. a free mirror before a metered one), a failed
    member falls through to the next one. Losing groups are left
    running, their requests end on their own timeout and their
    results are dropped.

    The engine never reads nor writes the selection record (the gui.db
    row of the pack mirrors): run() returns the winner and the
    subclass stores it, see ServerFile.probe.
    """

    def __init__(self, mirrors):
        """
        Args:
            mirrors (Mirrors | str | dict): The mirror input, see
                Mirrors.from_input() for the accepted forms
        """
        self.mirrors = Mirrors.from_input(mirrors)

    def run(self):
        """
        Race the candidates of every group and return the first usable
        one.

        A subclass usually wraps this entry to add its own side
        effects around the engine result, e.g. ServerFile.probe
        records the winner.

        Returns:
            tuple[str, Any]: Name and result of the selected candidate

        Raises:
            AllMirrorsFailedError: If no candidate of any group is
                usable, the message carries every failure
        """
        names = [name for members in self.mirrors.groups.values() for name in members]
        logger.info(f'Probing mirrors: {", ".join(names)}')
        results = Queue()
        for members in self.mirrors.groups.values():
            THREAD_POOL.start_thread_soon(self._probe_group, members, results)
        failed = 0
        reasons = []
        while failed < len(self.mirrors.groups):
            ok, name, result, reason = results.get()
            if ok:
                logger.attr('Mirror', name)
                return name, result
            failed += 1
            reasons.append(reason)
        raise AllMirrorsFailedError('; '.join(reasons))

    def probe_function(self, name, url):
        """
        Probe one candidate, the probe implementation of a subclass.

        Args:
            name (str): Candidate name, for the logs and the errors
            url (str): Candidate url

        Returns:
            Any: Result of a usable candidate, handed to the caller of
                run()

        Raises:
            Exception: The candidate is not usable when the function
                raises, the group falls through to its next member
        """
        raise NotImplementedError

    def _probe_group(self, members, results):
        """
        Probe the members of one group in order and put the result
        into the queue of the probe.

        Args:
            members (dict[str, str]): {name: url} of the group in the
                declared order
            results (Queue): Queue shared by the group workers:
                (True, name, result, '') when a member is usable, or
                (False, '', None, reasons) when the group is exhausted
        """
        reasons = []
        for name, url in members.items():
            start = perf_counter()
            try:
                result = self.probe_function(name, url)
            except Exception as e:
                # one candidate must never hang the probe: every
                # failure (http error, malformed payload, anything a
                # broken candidate raises) makes it unusable and the
                # group worker always reports back
                logger.warning(f'Mirror "{name}" is not usable: {type(e).__name__}: {e}')
                reasons.append(f'{name}: {type(e).__name__}: {e}')
                continue
            logger.info(f'Mirror "{name}" responded in {(perf_counter() - start) * 1000:.0f}ms')
            results.put((True, name, result, ''))
            return
        results.put((False, '', None, '; '.join(reasons)))
