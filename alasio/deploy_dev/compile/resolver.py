"""
Resolve the dependencies of a project into the pins of a lock.

The resolver is a greedy search with backtracking: it decides a version for
every requirement of a round, reads the dependencies those versions declare,
and moves on; when a requirement cannot be satisfied, it returns to the most
recent choice that has an untried candidate and takes the next one. That is
enough for a dependency set of a mod (40 to 90 distributions, see the design
note), where a full CDCL solver would be more machinery than the problem
deserves.

The rules it is built on:

- **highest first**, or **lowest** when the caller asks (the CI matrix of a
  project resolves the lowest versions a declaration allows);
- **prefer the existing pin**: the version a previous lock pinned is tried
  first when it still satisfies the requirements, which is what keeps a
  recompile from moving every distribution at once (the behaviour of
  pip-compile and of uv);
- **a wheel of the target, always**: a version the target interpreter cannot
  install is not a candidate, see alasio.deploy_dev.compile.candidates. A
  compile produces the versions pip installs, and it builds no sdist;
- **the requirements of a version are read once**: the METADATA of
  ``(index, name, version)`` is cached by the provider, so a version tried
  twice (by backtracking, or by a second parent) costs no request;
- **a frontier per round trip**: the versions of a level are decided in one
  round (their pages are read at once), and the expansion that follows reads
  the METADATA of the whole level at once, so the network rounds follow the
  depth of the dependency chain rather than its width. The pages of the names
  a level declares are read by the level itself, so they overlap its metadata
  instead of costing a round of their own;
- **the graph is walked ahead of the decisions**: a speculative walk (see
  _speculate) reads the page of every name as soon as it is discovered, picks
  the version the decisions would pick now, reads its METADATA, and walks its
  dependencies in turn. The decisions never change; they only find their
  answers in the caches, so a level costs no round trip of its own and the
  run waits for the network, not for its own rounds. A guess that misses is
  one request of a version the resolution does not take;

The decision the resolver makes for a name is the version, and the index it
read that version from: the index is the ``# from`` annotation of the lock, and
the pack server reads it to fetch the file a client installs.

Usage:
    from alasio.deploy_dev.compile.resolver import Resolver

    resolver = Resolver(provider, indexes, target)
    resolution = await resolver.resolve(roots)
    resolution.pins, resolution.sources, resolution.parents
"""

from typing import Dict, Tuple

import trio
from msgspec import Struct

try:
    BaseExceptionGroup
except NameError:  # python 3.8 / 3.9
    from exceptiongroup import BaseExceptionGroup

from alasio.deploy.pip_download._version import InvalidVersion, Version
from alasio.deploy.pip_download.pypi_file import WheelNotFoundError
from alasio.deploy_dev.compile.candidates import sdist_versions_of, versions_of
from alasio.deploy_dev.compile.errors import CompileError
from alasio.deploy_dev.compile.requirement import compile_environment, dependencies_of
from alasio.deploy_dev.metadata import MetadataError, readable_wheels
from alasio.logger import logger

__all__ = [
    'PROJECT_ORIGIN',
    'Resolution',
    'Resolver',
]

# The origin of a requirement the project declares itself, the annotation a
# lock writes as '<project> (pyproject.toml)'
PROJECT_ORIGIN = 'pyproject.toml'

# The candidates a name is tried with before the resolution gives up on it:
# a name whose last 30 versions all conflict is a declaration to fix, not a
# search to spend the run on
MAX_CANDIDATES = 30

# The backtracking steps one resolution may take, the bound of the search
MAX_BACKTRACKS = 200


class Resolution(Struct):
    """
    The pins a compile decided, and what they were read from.

    Attributes:
        pins (Dict[str, str]): PEP 503 name -> version, the lock
        sources (Dict[str, str]): PEP 503 name -> base url of the index that
            serves the pinned version
        parents (Dict[str, Tuple[str, ...]]): PEP 503 name -> the requirements
            that asked for it, the ``# via`` annotation of the lock
        extras (Dict[str, Tuple[str, ...]]): PEP 503 name -> the extras that
            were expanded for it
    """
    pins: Dict[str, str] = {}
    sources: Dict[str, str] = {}
    parents: Dict[str, Tuple[str, ...]] = {}
    extras: Dict[str, Tuple[str, ...]] = {}


class _Conflict:
    """
    One requirement that no pinned or available version satisfies.

    Attributes:
        name (str): PEP 503 name of the requirement
        reason (str): Why the requirement cannot be satisfied
        origin (str): The distribution whose requirement caused it, '' when
            the project declares it. The resolver retries that decision first,
            see Resolver._backtrack_target
    """

    __slots__ = ('name', 'origin', 'reason')

    def __init__(self, name, reason, origin=''):
        self.name = name
        self.reason = reason
        self.origin = origin


class _State:
    """
    The requirements known so far, and the pins decided so far.

    A state is rebuilt from the roots and the surviving decisions when the
    resolver backtracks, so a decision taken after the backtracked one leaves
    nothing behind: every collection here is derived from the decisions.

    Attributes:
        constraints (dict[str, list[RequirementSpec]]): The requirements of a
            name, in the order they were discovered
        pins (dict[str, str]): The version decided for a name
        sources (dict[str, str]): The index the pinned version was read from
        parents (dict[str, set[str]]): Who asked for a name
        extras (dict[str, set[str]]): The extras asked for a name
        expanded (dict[str, set[str]]): The extras whose dependencies were
            already added
    """

    __slots__ = ('constraints', 'expanded', 'extras', 'parents', 'pins', 'sources')

    def __init__(self, roots):
        """
        Args:
            roots (Iterable[RequirementSpec]): The requirements of the project
        """
        self.constraints = {}
        self.pins = {}
        self.sources = {}
        self.parents = {}
        self.extras = {}
        self.expanded = {}
        for spec in roots:
            self.add(spec)

    def add(self, spec):
        """
        Add one requirement to the state.

        Args:
            spec (RequirementSpec): The requirement
        """
        self.constraints.setdefault(spec.name, []).append(spec)
        self.parents.setdefault(spec.name, set()).add(spec.origin)
        self.extras.setdefault(spec.name, set()).update(spec.extras)

    def pin(self, name, candidate):
        """
        Decide the version of a name.

        Args:
            name (str): PEP 503 name
            candidate (Candidate): The version and the index it is read from
        """
        self.pins[name] = candidate.version
        self.sources[name] = candidate.source

    def unresolved(self):
        """
        Returns:
            list[str]: The names that have requirements but no pin
        """
        return [name for name in self.constraints if name not in self.pins]

    def unexpanded(self):
        """
        The pins whose dependencies are not read yet, with the extras to read.

        Returns:
            list[tuple[str, str, set[str]]]: (name, version, extras), the
                extras being the ones not expanded yet
        """
        out = []
        for name, version in self.pins.items():
            wanted = self.extras.get(name, set()) | {''}
            new = wanted - self.expanded.get(name, set())
            if new:
                out.append((name, version, new))
        return out


class _Decision:
    """
    One choice of the resolver, and the candidates it may still try.

    Attributes:
        name (str): PEP 503 name
        candidates (list[Candidate]): The candidates, most preferred first
        index (int): The candidate in use
    """
    __slots__ = ('candidates', 'index', 'name')

    def __init__(self, name, candidates):
        self.name = name
        self.candidates = candidates
        self.index = 0

    @property
    def candidate(self):
        """
        Returns:
            Candidate: The candidate in use
        """
        return self.candidates[self.index]

    def advance(self):
        """
        Returns:
            bool: True when an untried candidate is left
        """
        self.index += 1
        return self.index < len(self.candidates)


class Resolver:
    """
    Decide the version of every distribution of a requirement set.

    One instance is one compile: the pages and the metadata it reads are
    cached by the provider, the rejected candidates and the resolved names are
    the state of this run.

    Attributes:
        indexes (Tuple[Index, ...]): The indexes, the primary first
        target (TargetPython): The interpreter the lock is compiled for
        existing (dict[str, str]): The pins of the previous lock
        resolution (str): 'highest' or 'lowest'
    """

    def __init__(self, provider, indexes, target, existing=None, resolution='highest',
                 sources=None, max_candidates=MAX_CANDIDATES, max_backtracks=MAX_BACKTRACKS):
        """
        Args:
            provider (MetadataProvider): The reader of the index pages and the
                metadata of a version
            indexes (Iterable[Index]): The indexes to read, the primary first
            target (TargetPython): The interpreter the lock is compiled for
            existing (dict[str, str], optional): The pins of the previous lock,
                preferred while they still satisfy the requirements. Defaults
                to None, no preference
            resolution (str): 'highest' for the newest version a constraint
                allows, 'lowest' for the oldest. Defaults to 'highest'
            sources (dict[str, str], optional): The index a name is read from
                when its declaration names one, see ``[tool.uv.sources]``.
                Defaults to None, every name is read from every index
            max_candidates (int): The candidates tried per name. Defaults to
                MAX_CANDIDATES
            max_backtracks (int): The backtracking steps one resolution may
                take. Defaults to MAX_BACKTRACKS
        """
        if resolution not in ('highest', 'lowest'):
            raise ValueError(
                f'resolution must be "highest" or "lowest", got {resolution!r}')
        self.provider = provider
        self.indexes = tuple(indexes)
        self.target = target
        self.existing = dict(existing or {})
        self.resolution = resolution
        self.declared_sources = dict(sources or {})
        self.max_candidates = max(int(max_candidates), 1)
        self.max_backtracks = max(int(max_backtracks), 0)
        self.environment = compile_environment(target)
        # the answers of the run, the provider caches the requests themselves
        self._pages = {}        # {name: [(Index, PackageIndex)]}
        self._page_errors = {}  # {name: [(Index, error)]}
        self._page_flights = {}  # {name: trio.Event}, the page reads in flight
        self._versions = {}     # {name: list[Candidate]}
        self._sdist = {}        # {name: set[str]}
        self._metadata_errors = {}  # {(name, version): reason}

        # the state of the search, read by the speculative walk
        self._state = None
        # the nursery the speculative walk starts from, see _speculate
        self._spec_nursery = None
        self._speculating = set()  # {name}, the names a walk was started for
        self._speculated = set()   # {(name, version)}, the versions read ahead

    """
    The resolution
    """

    async def resolve(self, roots):
        """
        Decide the version of every requirement and of its dependencies.

        Args:
            roots (Iterable[RequirementSpec]): The requirements of the project

        Returns:
            Resolution: The pins, their sources, and who asked for them

        Raises:
            CompileError: If a requirement cannot be satisfied, or an index or
                a metadata cannot be read
        """
        roots = list(roots)
        try:
            # the walk has a nursery of its own, so it can be stopped without
            # touching the search: trio gives every task the scope of the
            # nursery that started it, never the scope of the code that called
            # start_soon, so a cancel scope around this body would leave the
            # walk running
            async with trio.open_nursery() as walk:
                self._spec_nursery = walk
                try:
                    return await self._search(roots)
                finally:
                    self._spec_nursery = None
                    # the walk is a guess: it stops with the search instead of
                    # holding the answer back
                    walk.cancel_scope.cancel()
        except BaseExceptionGroup as group:
            # a nursery wraps the exception of its body: the caller of a compile
            # catches CompileError, so a group of one is unwrapped here
            raise _leaf(group) from None

    async def _search(self, roots):
        """
        The search itself, see resolve().

        Args:
            roots (list[RequirementSpec]): The requirements of the project

        Returns:
            Resolution: The pins, their sources, and who asked for them

        Raises:
            CompileError: If a requirement cannot be satisfied, or an index or
                a metadata cannot be read
        """
        state = self._state = _State(roots)
        # the indexes of the compile are known before the first request: a
        # source whose host is in the table costs no probe, and a run over
        # known sources starts at full concurrency (see KNOWN_SOURCES)
        self.provider.bootstrap(index.url for index in self.indexes)
        for spec in roots:
            # the roots are what the compile is about: their pages and the
            # metadata of the version they will pick are read at once
            self._speculate(spec.name, (spec,))
        decisions = []
        backtracks = 0
        while True:
            conflict = await self._expand(state)
            if conflict is None:
                frontier = await self._frontier(state)
                if not frontier:
                    return self._finish(state)
                conflict = self._pin_all(state, decisions, frontier)
                if conflict is None:
                    continue
            logger.info(f'Conflict on "{conflict.name}": {conflict.reason}')
            while decisions:
                position = self._backtrack_target(decisions, conflict)
                # the decisions after the one being retried depend on it
                del decisions[position + 1:]
                frame = decisions[position]
                if not frame.advance():
                    decisions.pop()
                    continue
                backtracks += 1
                if backtracks > self.max_backtracks:
                    raise CompileError(
                        f'The requirements cannot be resolved, {self.max_backtracks} '
                        f'backtracking steps were spent. The last conflict is: '
                        f'{self._describe(conflict, state)}')
                state = self._state = self._replay(roots, decisions)
                logger.info(
                    f'Retry "{frame.name}" with {frame.candidate.version}')
                break
            else:
                raise CompileError(self._describe(conflict, state))

    @staticmethod
    def _backtrack_target(decisions, conflict):
        """
        The decision to retry when a requirement cannot be satisfied.

        The search retries the decision of the conflicting name when it has
        one, then the decision of the distribution that required it: those are
        the choices that can change the conflict. The most recent decision is
        the fallback, which is the choice that added the requirement.

        Args:
            decisions (list[_Decision]): The decisions in force, in order
            conflict (_Conflict): The requirement that cannot be satisfied

        Returns:
            int: Position of the decision to retry in the list
        """
        names = {conflict.name}
        if conflict.origin:
            names.add(conflict.origin)
        for position in range(len(decisions) - 1, -1, -1):
            if decisions[position].name in names:
                return position
        return len(decisions) - 1

    def _replay(self, roots, decisions):
        """
        Rebuild the state from the roots and the surviving decisions.

        Args:
            roots (Iterable[RequirementSpec]): The requirements of the project
            decisions (list[_Decision]): The decisions in force

        Returns:
            _State: The state, the dependencies to be expanded again (the
                metadata of each version is cached, so this costs no request)
        """
        state = _State(roots)
        for decision in decisions:
            state.pin(decision.name, decision.candidate)
        return state

    def _finish(self, state):
        """
        Build the answer of the run from the resolved state.

        Args:
            state (_State): The state with every requirement decided

        Returns:
            Resolution: The pins, sorted by name
        """
        names = sorted(state.pins)
        return Resolution(
            pins={name: state.pins[name] for name in names},
            sources={name: state.sources[name] for name in names},
            parents={name: tuple(sorted(state.parents.get(name, ()))) for name in names},
            extras={name: tuple(sorted(state.extras.get(name, ()))) for name in names},
        )

    """
    The expansion of a pinned version
    """

    async def _expand(self, state):
        """
        Read the dependencies of every pinned version, level by level.

        The versions of one level are read concurrently (one round trip per
        level, not one per distribution), and the requirements they declare
        are added to the state. A requirement that a pinned version does not
        satisfy is the conflict of the level: the resolution backtracks.

        Args:
            state (_State): The state to expand

        Returns:
            _Conflict | None: The first requirement that cannot be satisfied,
                None when the state is expanded

        Raises:
            CompileError: If a metadata or an index cannot be read
        """
        while True:
            todo = state.unexpanded()
            if not todo:
                return None
            todo.sort(key=lambda item: item[0])
            outcomes = await self._metadata_of(todo, state)
            conflict = None
            for (name, version, extras), (meta, specs) in zip(todo, outcomes):
                if meta is None:
                    conflict = conflict or _Conflict(
                        name, f'the metadata of "{name}=={version}" cannot be read')
                    continue
                for spec in specs:
                    state.add(spec)
                    pinned = state.pins.get(spec.name)
                    if pinned is not None and not self._satisfies(spec, pinned):
                        conflict = conflict or _Conflict(
                            spec.name,
                            f'"{name}=={version}" requires "{spec.name}{spec.specifier}", '
                            f'the pinned version is "{pinned}"',
                            spec.origin)
                state.expanded.setdefault(name, set()).update(extras)
            if conflict is not None:
                return conflict

    async def _metadata_of(self, todo, state):
        """
        Read the metadata of a level, concurrently.

        The dependencies of a version are read here, once, and the pages of the
        names they declare are asked for in the same nursery: the page round
        of the next level overlaps the metadata round of this one instead of
        following it.

        Args:
            todo (list[tuple[str, str, set[str]]]): (name, version, extras)
            state (_State): The state of the run, the source of a pin

        Returns:
            list[tuple[DistributionMetadata | None, list[RequirementSpec]]]:
                The metadata of every entry and the dependencies it declares,
                (None, []) for the version whose metadata cannot be read

        Raises:
            CompileError: If a request fails: an unreachable index or a broken
                transport is not a candidate to skip, it is the run failing
        """
        out = [(None, [])] * len(todo)
        started = set()
        # the failure of one entry, (name, version, source, error): a failure
        # that is not a property of the version stops the round instead of
        # picking another one, and it is raised outside the nursery so the
        # caller reads it as the CompileError it is, not as the exception group
        # of a cancelled nursery
        failures = []

        async def one(position, name, version, extras, source, nursery):
            try:
                meta = await self.provider.metadata(name, version, source)
            except MetadataError as e:
                logger.warning(f'"{name}=={version}" has no readable metadata: {e}')
                self._metadata_errors[(name, version)] = str(e)
                return
            except Exception as e:
                failures.append((name, version, source, e))
                nursery.cancel_scope.cancel()
                return
            try:
                specs = dependencies_of(
                    meta.requires_dist, extras, self.environment, name)
            except CompileError as e:
                failures.append((name, version, source, e))
                nursery.cancel_scope.cancel()
                return
            out[position] = (meta, specs)
            for spec in specs:
                if spec.name in state.pins:
                    continue
                self._speculate(spec.name, (spec,))
                if spec.name in self._pages or spec.name in started:
                    continue
                started.add(spec.name)
                nursery.start_soon(self._read_pages, spec.name)

        async with trio.open_nursery() as nursery:
            for position, (name, version, extras) in enumerate(todo):
                nursery.start_soon(
                    one, position, name, version, extras, state.sources[name], nursery)
        if failures:
            name, version, source, error = failures[0]
            if isinstance(error, CompileError):
                raise error
            # the provider raises the http errors of the transport and of the
            # status: neither is a property of this version
            raise CompileError(
                f'Cannot read "{name}=={version}" from "{source}": {error}') from error
        return out

    def _source_of(self, name):
        """
        The index a name is read from when its declaration names one.

        Args:
            name (str): PEP 503 name

        Returns:
            str: The base url of the index, '' when the name is read from
                every index of the compile
        """
        return self.declared_sources.get(name, '')

    """
    The selection of a candidate
    """

    async def _frontier(self, state):
        """
        The requirements to decide this round, after reading the pages they need.

        A direct requirement of the project is decided before a transitive
        one, an exact pin before a range, and the rest in the order they were
        discovered: the same order uv resolves in, which keeps a compile
        reproducible.

        Args:
            state (_State): The state of the run

        Returns:
            list[str]: The names to decide, in the order they are decided. An
                empty list when every requirement is decided
        """
        pending = state.unresolved()
        if not pending:
            return []
        await self._prefetch(pending)
        order = {name: position for position, name in enumerate(state.constraints)}
        return sorted(pending, key=lambda name: (
            not any(spec.origin == PROJECT_ORIGIN for spec in state.constraints[name]),
            not any('==' in spec.specifier for spec in state.constraints[name]),
            order[name],
        ))

    def _pin_all(self, state, decisions, names):
        """
        Decide the version of every requirement of the frontier.

        The decisions are taken one after the other from the constraints known
        at this round, and every one is kept: the expansion that follows reads
        the metadata of the whole frontier at once (one round trip for the
        round, not one per distribution), so a constraint a distribution of the
        same round declares is discovered by the expansion and taken back by
        the backtracking, not by a longer decision loop.

        Args:
            state (_State): The state of the run
            decisions (list[_Decision]): The decisions in force, appended here
            names (Iterable[str]): The names to decide, the order of the list
                is the order of the decisions

        Returns:
            _Conflict | None: The requirement no version satisfies, None when
                every name of the frontier is decided
        """
        for name in names:
            candidates = self._candidates(name, state.constraints.get(name, ()))
            if not candidates:
                return _Conflict(name, f'no version of "{name}" matches its requirements')
            decisions.append(_Decision(name, candidates))
            state.pin(name, candidates[0])
        return None

    async def _prefetch(self, names):
        """
        Read the pages of some names concurrently, the ones not read yet.

        The pages of the names a level declares are read by the level itself
        (see _metadata_of), so this is the first frontier of the run, and the
        names a backtracking step left unread.

        Args:
            names (Iterable[str]): PEP 503 names
        """
        todo = [name for name in names if name not in self._pages]
        if not todo:
            return
        async with trio.open_nursery() as nursery:
            for name in todo:
                nursery.start_soon(self._read_pages, name)

    async def _read_pages(self, name):
        """
        Read the pages of one name from every index it may come from.

        The read happens once per name: the search and the speculative walk
        (see _speculate) ask for the same names, and the second caller waits
        for the read of the first instead of sending its own requests.

        An index that does not serve the distribution is not an error: a
        private package is only in the private index, a backport index only
        serves some names. The errors are kept for the conflict report.

        Args:
            name (str): PEP 503 name
        """
        if name in self._pages:
            return
        flight = self._page_flights.get(name)
        if flight is not None:
            await flight.wait()
            return
        flight = self._page_flights[name] = trio.Event()
        try:
            await self._read_pages_once(name)
        finally:
            flight.set()

    async def _read_pages_once(self, name):
        """
        Read the pages of one name and the candidates of them, see _read_pages.

        Args:
            name (str): PEP 503 name
        """
        sources = self._sources_of(name)
        pages = [None] * len(sources)
        errors = []

        async def one(position, index):
            try:
                pages[position] = (index, await self.provider.page(name, index.url))
            except Exception as e:
                errors.append((index, e))

        async with trio.open_nursery() as nursery:
            for position, index in enumerate(sources):
                nursery.start_soon(one, position, index)
        self._pages[name] = [page for page in pages if page is not None]
        self._page_errors[name] = errors
        if not self._pages[name]:
            logger.warning(
                f'No index serves "{name}": '
                f'{"; ".join(f"{index.text}: {error}" for index, error in errors)}')
        # the files of the pages are grouped into versions here: the work is
        # CPU (the page of numpy lists hundreds of wheels), and it runs while
        # the other pages of the round are still in flight
        self._versions_of(name)

    """
    The speculative walk
    """

    def _speculate(self, name, specs):
        """
        Start reading ahead of the decisions, without waiting for them.

        The decision loop needs a whole level of metadata before it decides
        the level, so a level would cost one round trip and the next level
        would start only after it. The walk breaks that: as soon as a name is
        discovered, its pages are read, the version a decision would pick now
        is chosen, and that METADATA is read, whose dependencies are walked in
        turn. The decisions are not changed by any of it, they only find their
        answers already in the caches of the provider.

        The price is a read of a version the final resolution may not take
        (a second parent narrows the constraint later). It is one request per
        name at most, and it is the request that would have been made first
        anyway.

        A speculative read never fails the run: a version the resolution never
        asks for has no right to break the compile, and the real read reports
        its own error.

        Args:
            name (str): PEP 503 name the walk continues from
            specs (Iterable[RequirementSpec]): The requirements that ask for
                the name, the constraint the first guess is made with
        """
        nursery = self._spec_nursery
        if nursery is None or name in self._speculating or name in self._pages:
            # outside resolve(), or the name is walked (or read) already. A walk
            # runs once per name: uv walks a name again when its range changes,
            # but the levels of this resolver are decide-then-expand, so a
            # narrower constraint of a later level arrives before the next
            # round anyway, and a walk that misses costs one request
            return
        self._speculating.add(name)
        nursery.start_soon(self._speculate_one, name, tuple(specs))

    async def _speculate_one(self, name, specs):
        """
        Read one name ahead of the decisions, see _speculate.

        Args:
            name (str): PEP 503 name
            specs (tuple[RequirementSpec, ...]): The requirements that ask for
                it
        """
        try:
            await self._read_pages(name)
            state = self._state
            if state is None or name in state.pins:
                return
            known = list(state.constraints.get(name, ())) + list(specs)
            candidate = self._guess(name, known)
            if candidate is None:
                return
            key = (name, candidate.version)
            if key in self._speculated:
                return
            if not self._reads_a_small_file(name, candidate):
                # a source known to serve no range answers this guess with the
                # whole file: reading it ahead of a decision that may drop it is
                # the one read a walk must not pay for
                logger.debug(
                    f'No speculative read of "{name}=={candidate.version}": '
                    f'it would transfer the whole file')
                return
            self._speculated.add(key)
            extras = {extra for spec in known for extra in spec.extras}
            extras.update(state.extras.get(name, ()))
            meta = await self.provider.metadata(name, candidate.version, candidate.source)
            for child in dependencies_of(
                meta.requires_dist, extras, self.environment, name
            ):
                if child.name in state.pins:
                    continue
                self._speculate(child.name, (child,))
        except Exception as e:
            # a walk is a guess: the resolution reports what it really reads
            logger.debug(f'The speculative read of "{name}" stopped: {e}')

    def _guess(self, name, specs):
        """
        The version the decisions would pick for a name with these requirements.

        Args:
            name (str): PEP 503 name
            specs (List[RequirementSpec]): The known requirements of the name

        Returns:
            Candidate | None: The candidate to read ahead, None when no version
                matches or the constraints do not parse
        """
        try:
            candidates = self._candidates(name, specs)
        except CompileError:
            # a constraint that does not parse is the failure of the decision
            # that reads it, not of a walk that saw it first
            return None
        return candidates[0] if candidates else None

    def _reads_a_small_file(self, name, candidate):
        """
        Whether the metadata of a version is a few requests, not a whole file.

        A version costs one small request when a wheel of it serves a PEP 658
        metadata file, and a few range requests when the source serves a range.
        A version whose only file the source would send whole is not read ahead
        of a decision: the walk may drop it, and a wheel of tens of MiB is not
        worth transferring twice.

        The answer only uses what the run knows already: a source no request has
        reached yet reads as cheap, the rule of uv (see
        SourceCapabilities), so the guard never spends a request of its own.

        Args:
            name (str): PEP 503 name
            candidate (Candidate): The version the walk would read

        Returns:
            bool: True when the metadata is cheap to read ahead
        """
        page = next(
            (page for index, page in self._pages.get(name, ()) if index.url == candidate.source),
            None)
        if page is None:
            return True
        try:
            wheels = readable_wheels(page.links, name, candidate.version)
        except WheelNotFoundError:
            # a version that publishes no wheel: the read is its source
            # distribution, a whole archive
            return False
        if any(wheel.link.metadata_url for wheel in wheels):
            return True
        capabilities = self.provider.capabilities_of(candidate.source)
        if capabilities is None:
            return True
        return capabilities.range is not False or capabilities.suffix_range is not False

    def _sources_of(self, name):
        """
        The indexes a name may come from, in the preference order.

        An extra index is read before the primary one: when both publish the
        same version, the lock takes the file of the extra index. The extra
        index exists for a reason of this project (the backport index of the
        shipped runtime, or a private index), so it is the index that publishes
        the build the project wants, and a version it does not carry at all is
        still served by the primary one (the versions are compared across every
        index, the preference only decides who serves a version both have).

        Args:
            name (str): PEP 503 name

        Returns:
            list[Index]: The indexes, the one the requirement names when it
                names one, otherwise the extra indexes in the declared order
                and the primary index last
        """
        declared = self._source_of(name)
        if declared:
            return [index for index in self.indexes if index.url == declared]
        extra = [index for index in self.indexes if not index.primary]
        primary = [index for index in self.indexes if index.primary]
        return extra + primary

    def _candidates(self, name, specs):
        """
        The candidates of one requirement, most preferred first.

        The constraints of every parent are intersected, the existing pin is
        moved first when it still satisfies them, and the prereleases are only
        offered when a constraint names one (PEP 440).

        The pages of the name are read already: the caller decided the name
        from the frontier, which read them (see _frontier).

        Args:
            name (str): PEP 503 name
            specs (Iterable[RequirementSpec]): The requirements of the name

        Returns:
            list[Candidate]: The candidates to try, most preferred first

        Raises:
            CompileError: If a constraint does not parse
        """
        versions = self._versions_of(name)
        specs = list(specs)
        specifier = self._specifier_of(name, specs)
        # a constraint that names a prerelease asks for one, and every other
        # constraint of the name then considers them (PEP 440)
        prereleases = False
        for spec in specs:
            if not spec.specifier:
                continue
            try:
                prereleases = prereleases or _names_prerelease(spec.specifiers)
            except ValueError as e:
                raise CompileError(str(e)) from None

        out = []
        for candidate in versions:
            if candidate.prerelease and not prereleases:
                continue
            if specifier is not None and not specifier.contains(
                candidate.version, prereleases=prereleases
            ):
                continue
            out.append(candidate)
        if self.resolution == 'lowest':
            out.reverse()

        existing = self.existing.get(name, '')
        if existing:
            for position, candidate in enumerate(out):
                if candidate.version == existing:
                    out.insert(0, out.pop(position))
                    break
        if len(out) > self.max_candidates:
            out = out[:self.max_candidates]
        return out

    def _versions_of(self, name):
        """
        The candidates of a name, read once per run.

        Args:
            name (str): PEP 503 name

        Returns:
            list[Candidate]: The candidates, highest first
        """
        cached = self._versions.get(name)
        if cached is not None:
            return cached
        pages = self._pages.get(name, [])
        candidates = versions_of(name, pages, self.target)
        self._versions[name] = candidates
        return candidates

    def _specifier_of(self, name, specs):
        """
        The intersection of the constraints of a name.

        Each constraint is parsed once, by the cached view of the requirement,
        and the sets are intersected: a name required by three parents is one
        set of their specifiers, never a text joined and parsed again.

        Args:
            name (str): PEP 503 name
            specs (Iterable[RequirementSpec]): The requirements of the name

        Returns:
            SpecifierSet | None: The specifier, None when no constraint pins one

        Raises:
            CompileError: If a constraint does not parse
        """
        sets = []
        for spec in specs:
            if not spec.specifier:
                continue
            try:
                sets.append(spec.specifiers)
            except ValueError as e:
                raise CompileError(str(e)) from None
        if not sets:
            return None
        combined = sets[0]
        for other in sets[1:]:
            combined = combined & other
        return combined

    @staticmethod
    def _satisfies(spec, version):
        """
        Whether a version satisfies one requirement.

        Args:
            spec (RequirementSpec): The requirement
            version (str): Version of the pinned distribution

        Returns:
            bool: True when the requirement allows the version
        """
        if not spec.specifier:
            return True
        try:
            return spec.specifiers.contains(version, prereleases=True)
        except ValueError as e:
            raise CompileError(str(e)) from None

    """
    The report
    """

    def _describe(self, conflict, state):
        """
        The message of a resolution that cannot be satisfied.

        Args:
            conflict (_Conflict): The conflict that stopped the search
            state (_State): The state that was being resolved

        Returns:
            str: The message, the chain that asked for the name and the
                versions that were available
        """
        name = conflict.name
        specs = state.constraints.get(name, [])
        wanted = ', '.join(sorted({spec.specifier for spec in specs if spec.specifier}))
        lines = [f'No version of "{name}" matches "{wanted or "any version"}".']
        # every requirement of the name: the chain a reader has to see to know
        # which declaration to change
        for spec in specs[:5]:
            lines.append(f'  required by: {spec.origin} -> {name}{spec.specifier}')
        if conflict.reason:
            lines.append(f'  {conflict.reason}')
        available = self._versions.get(name) or []
        if available:
            shown = ', '.join(candidate.version for candidate in available[:8])
            lines.append(f'  candidates: {shown}')
        else:
            errors = self._page_errors.get(name) or []
            for index, error in errors:
                lines.append(f'  {index.text}: {error}')
        # a version whose metadata could not be read, which is why it was not
        # a candidate of the constraint
        for (failed, version), reason in list(self._metadata_errors.items())[:3]:
            if failed == name:
                lines.append(f'  {name}=={version}: {reason}')
        sdist = self._sdist_of(name)
        if sdist:
            shown = ', '.join(sorted(sdist)[:8])
            lines.append(
                f'  source distribution only: {shown} (its dependencies are read from '
                f'the archive)')
        return '\n'.join(lines)

    def _sdist_of(self, name):
        """
        The versions of a name that only have a source distribution.

        Args:
            name (str): PEP 503 name

        Returns:
            set[str]: The version texts, the empty set when the page read did
                not happen or has no sdist
        """
        cached = self._sdist.get(name)
        if cached is None:
            cached = sdist_versions_of(name, self._pages.get(name, []))
            self._sdist[name] = cached
        return cached


def _leaf(group):
    """
    The exception a nursery raised, without the group of a single failure.

    A nursery wraps the exception of its body in a group. The caller of a
    compile catches CompileError, so a group that holds one exception is
    unwrapped down to it; a group of several is a real answer of several tasks
    and is raised as it is.

    Args:
        group (BaseExceptionGroup): The group the nursery raised

    Returns:
        BaseException: The only exception of the group, or the group
    """
    while True:
        if len(group.exceptions) != 1:
            return group
        only = group.exceptions[0]
        if not isinstance(only, BaseExceptionGroup):
            return only
        group = only


def _names_prerelease(specifiers):
    """
    Whether a specifier set names a prerelease, which turns prereleases back on.

    A specifier that writes a prerelease (``>=2.0rc1``) asks for one, and a
    resolution that would otherwise refuse every prerelease has to consider
    them (PEP 440, the rule of pip and of uv).

    Args:
        specifiers (SpecifierSet): The parsed constraint of a requirement

    Returns:
        bool: True when a version the set names is a prerelease
    """
    for spec in specifiers:
        try:
            if Version(spec.version).is_prerelease:
                return True
        except InvalidVersion:
            continue
    return False
