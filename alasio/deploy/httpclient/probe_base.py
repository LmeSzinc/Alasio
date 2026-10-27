"""
Shared design of the mirror probes: the identity of one probe and the flow the
flavors implement.

A probe is one candidate set (Mirrors) with an identity inside its scene:

    ProbeBase(mirrors, name='')      name: the DeployJob(name=...) key of the set
    ServerFile.SCENE = 'repo'        scene: the usage domain, a class attribute

The identity keys the selection record (probe_db), the in-process probe lock
and the logs of a probe: (scene, name), printed as `scene` for a set of its
own and `scene/name` for a named one (see ProbeBase.key). The unit of a probe
is the path of the request it
serves: the engine keys its bookkeeping with it (one probe per path per
instance) and the domain declares what a candidate must answer to serve it
(probe_function). A subclass overrides SCENE and implements the flow below;
the transfer and the concurrency are the flavor (probe_async for trio callers,
probe_sync for synchronous ones).

The flow of one request of a flow (the flavors implement it, the docstrings of
their resolve() reference this list):

- a usable record (probe_db.load_result) is the answer: the request goes to
  the recorded mirror, no probe runs;
- without a record the probe of the path runs first, under the flavor's
  probe_lock and with a second record read inside the lock (another flow of
  the process may just have probed and recorded). The winner is recorded
  (probe_db.save_result) and reused by every later request of the instance;
  the payload of the probe is handed over to the work of the flow when the
  probe was the request itself (see probe_function);
- a failed request (the failure types the domain declares) makes the instance
  probe the same path once - the mirror that failed stays a candidate, a
  transport failure may be a blip - and retry the request on the winner
  exactly once. The re-probe runs whether the mirror came from the record or
  from the probe of this very request: a probe only proves that a candidate
  serves the path (the default HEAD), not that its content is usable, see
  AsyncProbe.fetch(). A path an earlier request of this instance probed stays
  as it is (one probe per path per instance) and the failure is raised as the
  answer. No candidate that can serve the path: the original failure is raised
  as-is, the caller keeps its own fallback (see DeployJob.update()).

Every entry of the flow is best effort about the record: the database is a
hint, a failure of it only makes the next probe run (probe_db).
"""
from alasio.deploy.httpclient import probe_db
from alasio.deploy.httpclient.probe_mirrors import Mirrors


class ProbeBase:
    """
    The identity of one probe and the shape of the flow its flavor implements.

    Attributes:
        mirrors (Mirrors): The candidate structure of this probe
        name (str): Identity of the set inside the scene, '' allowed
        SCENE (str): Usage domain of the set, part of the record key
    """

    # the usage domain of the candidate set: 'repo' (the pack channels),
    # 'pypi', 'dns', ... It is part of the key of the selection record and of
    # the probe lock; a subclass overrides it. The default is a namespace of
    # the module itself, a subclass that forgets to override it never writes
    # into (or reads from) the record of another domain
    SCENE = 'probe'

    # the table of the selection record (probe_db): None selects the real
    # gui.db table (a singleton), the tests inject their in-memory table here
    RECORD_TABLE = None

    def __init__(self, mirrors, name=''):
        """
        Args:
            mirrors (Mirrors | str | dict): The candidate structure, see
                Mirrors.from_input() for the accepted forms
            name (str): Identity of this candidate set inside its scene: the
                DeployJob(name=...) key of the pack channels, '' for a set of
                its own. Part of the key of the record and of the probe lock.
                Defaults to ''
        """
        self.mirrors = Mirrors.from_input(mirrors)
        self.name = name
        # the mirror resolved for this instance (the record, or a probe of this
        # instance), '' until a request resolves it, see the flavor's resolve()
        self._mirror = ''
        # paths of the requests this instance already probed: one probe per
        # path per instance, shared by the probe of the first request and the
        # re-probe of a failed request
        self._probed = set()

    @property
    def key(self):
        """
        The identity of this candidate set as the logs print it, see
        probe_db.key_text(): `scene` for a set of its own, `scene/name` for a
        named one.

        Returns:
            str: The key of this set, e.g. '`repo`', '`repo/alas`'
        """
        return probe_db.key_text(self.SCENE, self.name)

    @property
    def mirror_url(self):
        """
        Url of the mirror resolved for this instance.

        The mirror is resolved by the record of the machine or by a probe of
        the first request, see the flavor's resolve().

        Returns:
            str: The url of the resolved mirror, '' when no mirror is resolved
                yet
        """
        return self.mirrors.url_of(self._mirror)

    # ------------------------------------------------------------------
    # The record (a hint, see probe_db): synchronous, the async flavor
    # wraps the calls in a worker thread
    # ------------------------------------------------------------------

    def load_record(self):
        """
        The mirror this candidate set last selected, '' when there is
        no usable record.

        Returns:
            str: The recorded mirror name, '' when a probe is needed
        """
        return probe_db.load_result(self.SCENE, self.name, self.mirrors, table=self.RECORD_TABLE)

    def save_record(self, result):
        """
        Record the mirror a probe of this instance selected.

        Args:
            result (str): Name of the winning mirror

        Raises:
            ValueError: If the name is not a mirror of the structure
        """
        probe_db.save_result(self.SCENE, self.name, self.mirrors, result, table=self.RECORD_TABLE)

    # ------------------------------------------------------------------
    # The per-path bookkeeping (one probe per path per instance)
    # ------------------------------------------------------------------

    def was_probed(self, path):
        """
        Whether this instance already probed a path (the probe of the
        first request, or the re-probe of a failed one). A path is probed
        at most once per instance: a later failure of it is raised as-is
        instead of racing again (the network environment does not change
        between the requests of one flow).

        Args:
            path (str): Path of a request

        Returns:
            bool: True when the path was probed by this instance
        """
        return path in self._probed

    def mark_probed(self, path):
        """
        Remember that this instance probed a path (the probe of the first
        request and the re-probe of a failure call this before their race, so
        a failure inside the race cannot probe it again).

        Args:
            path (str): Path of a request
        """
        self._probed.add(path)

    # ------------------------------------------------------------------
    # The hooks of a flavor and of the domain
    # ------------------------------------------------------------------

    async def run(self, path):
        """
        Race the candidates of every group for one path and return the
        first usable one. A flavor method: the async one runs the groups in
        a trio nursery, the sync one in a thread pool.

        Args:
            path (str): Path of the request the candidates are probed for

        Returns:
            tuple[str, Any]: Name and payload of the winner. The payload
                is the payload of probe_function() (the bytes the probe
                fetched), None when the probe only proved the availability

        Raises:
            AllMirrorsFailedError: If no candidate of any group is usable,
                the message carries every failure
        """
        raise NotImplementedError

    def probe_function(self, name, url, path):
        """
        Probe one candidate for one path: what a candidate must answer to
        serve it, and the payload the flow consumes when the probe is the
        request itself.

        A domain method of the flavor: the async flavors implement it with
        async def and await it, the sync ones block. The default probe of a
        path is a HEAD (2xx = the candidate serves it) and yields None as
        its payload: the flow requests the path itself afterwards. A domain
        whose path is short and whose content must be validated on the spot
        overrides this method, fetches the path and returns its payload, so
        the flow reuses it (ServerFile and its latest.pack).

        Args:
            name (str): Candidate name, for the logs and the errors
            url (str): Candidate url
            path (str): Path of the request to probe

        Returns:
            Any: The payload of the probe, None when the flow requests the
                path itself

        Raises:
            Exception: The candidate is not usable when the function
                raises, the group falls through to its next member
        """
        raise NotImplementedError

    def request(self, name, url, path):
        """
        Make the plain request of a flow on a chosen mirror, the work of a
        flow that passes none of its own.

        A domain method of the flavor: the async flavors implement it with
        async def and await it, the sync ones block.

        Args:
            name (str): Mirror name the request goes to, for the logs
            url (str): Base url of that mirror
            path (str): Path of the request

        Returns:
            Any: The payload the flow consumes (the bytes of the path in
                the HTTP flavors, already parsed and validated by a domain
                that parses in its own work)

        Raises:
            Exception: The failure types the domain declares are classified
                by the flavor and make it probe the path again, anything
                else propagates as-is
        """
        raise NotImplementedError
