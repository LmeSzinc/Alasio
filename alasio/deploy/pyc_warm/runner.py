"""
Run a warmup: scan the scope of every unit, then compile what is stale.

The scan and the compilation are two halves of one pass. The scan is IO
bound, it lists every folder of a unit once and reads the header of the
pyc files that may be stale; the compilation is CPU bound. A unit is
planned and dispatched as it is answered, so the pool is fed while the
scope reads the unit after it, and a run where everything is cached stops
after the scan, which is the common case: the second start of a release
reads about five thousand headers and writes nothing.

    for every unit  plan() its sources, batch them, hand the batches over
    in the pool      ensure() an item, compile_file() when it is stale

A failure is counted, never raised: the warmup is an optimization, an
environment that can not be warmed runs exactly like an environment that
was never warmed, see compile.py.

The threads are there to overlap the waits of the disk with the work of
the cpu, not to compile faster: a compile holds the GIL, so two of them
never run at once. What runs while a thread waits for a file is the other
thread, the listing of a folder or an other open.

The calling thread keeps the listing, and hands a batch over as soon as
it holds one: a folder listed twice pays twice, so a folder is never cut
between two tasks, while the header checks and the compilations are per
source and split freely.

One pool serves the whole run. The threads are created once, and a unit
that ends on a partial batch leaves its threads to the unit after it
instead of letting them idle at every boundary. A unit is waited for at
its end, which the measurement shows costs nothing (3.917 s against
3.927 s on a cold tree of 2400 sources) and keeps the counters of a unit
exact.

A unit is one ScopeResult, so the report is the scope: every distribution
the environment installed and every mod answers one line, including the
ones that were asked for and not found. A caller reads which part of a
release is warm and which part is missing without a second pass.

The report is logged, one line per unit: the module has no channel of its
own to write to, so a caller that wants the lines collected, or muted,
sets that on the logger, see __main__.
"""
import time

from alasio.backport.batch import batched
from alasio.deploy.pyc_warm.compile import compile_file
from alasio.deploy.pyc_warm.fresh import ensure, plan
from alasio.ext.concurrent.threadpool import ThreadPool
from alasio.logger import logger

# How many failure messages one unit keeps for the report, the count of
# failures is exact, the messages are samples
ERROR_SAMPLES = 3
# Threads of a warmup, the calling thread lists the folders and these read
# the headers and compile. Measured on a release of 6101 sources, a scan
# where every pyc file is fresh: 1.72 s with one thread, 0.43 s with four,
# 0.36 s with eight. Four keeps 96% of the gain of eight
WORKERS = 4
# Sources of one compilation task. A task is small enough for the tail of
# the pool to fill, and big enough that one task per source does not pay
# the pool for every file
COMPILE_BATCH = 32


class ResultReport:
    """
    What one unit of the scope did.

    Attributes:
        tier (str): Name of the tier the unit belongs to, e.g. "pack"
        target (str): The unit, a distribution name, a mod name, or the
            path of a file that could not be read
        sources (int): Number of sources the unit listed
        stale (int): Number of sources whose cache had to be compiled
        compiled (int): Number of pyc files written
        failed (int): Number of sources that could not be compiled
        seconds (float): Time of the scan and the compilation of the unit
        errors (list[str]): The first failure messages of the unit
    """

    def __init__(self, tier, target):
        """
        Args:
            tier (str): Name of the tier the unit belongs to
            target (str): The unit
        """
        self.tier = tier
        self.target = target
        self.sources = 0
        self.stale = 0
        self.compiled = 0
        self.failed = 0
        self.seconds = 0.0
        self.errors = []

    def line(self):
        """
        Returns:
            str: The unit as one line of the report, e.g.
                "pack/trio: 120 sources, 4 stale, 4 compiled, 0 failed, 1.12s"
        """
        return (
            f'{self.tier}/{self.target}: {self.sources} sources, {self.stale} stale, '
            f'{self.compiled} compiled, {self.failed} failed, {self.seconds:.2f}s'
        )

    def to_dict(self):
        """
        Returns:
            dict: The unit as a plain dict, the JSON form of the report
        """
        return {
            'tier': self.tier,
            'target': self.target,
            'sources': self.sources,
            'stale': self.stale,
            'compiled': self.compiled,
            'failed': self.failed,
            'seconds': round(self.seconds, 3),
            'errors': self.errors,
        }


class Report:
    """
    What one warmup run did, one entry per unit of the scope.

    Attributes:
        results (list[ResultReport]): The units in the order they ran,
            which is the order of the tiers and then the order of the
            units inside a tier
        seconds (float): Time of the whole run
    """

    def __init__(self):
        self.results = []
        self.seconds = 0.0

    @property
    def compiled(self):
        """
        Returns:
            int: Number of pyc files written by the run
        """
        return sum(result.compiled for result in self.results)

    @property
    def failed(self):
        """
        Returns:
            int: Number of sources the run could not compile
        """
        return sum(result.failed for result in self.results)

    @property
    def stale(self):
        """
        Returns:
            int: Number of sources the run had to compile
        """
        return sum(result.stale for result in self.results)

    @property
    def unresolved(self):
        """
        Returns:
            list[str]: Units the run asked for and did not find, e.g. a
                distribution that is not installed and a mod whose
                declaration names a requirements file the tree does not
                hold
        """
        return [result.target for result in self.results if not result.sources]

    def to_dict(self):
        """
        Returns:
            dict: The run as a plain dict, the JSON form of the report
        """
        return {
            'results': [result.to_dict() for result in self.results],
            'unresolved': self.unresolved,
            'compiled': self.compiled,
            'failed': self.failed,
            'stale': self.stale,
            'seconds': round(self.seconds, 3),
        }


def warm(scope, dry_run=False):
    """
    Warm the bytecode cache of the scope.

    The units come in the order of the scope: the pack distributions, the
    common ones, the other pins, then the mods. A unit is scanned and
    compiled before the next one is read, so a run that is interrupted has
    finished the units that matter, see scope.py.

    The lines of the report go to the logger at INFO, one for a unit and
    one for every source of a dry run.

    Args:
        scope (Scope): The sources to warm, see Scope.tiers()
        dry_run (bool): List the sources to compile without compiling
            them. Defaults to False

    Returns:
        Report: What the run did
    """
    start = time.perf_counter()
    report = Report()
    pool = None if dry_run else ThreadPool(pool_size=WORKERS)
    for result in scope.tiers():
        report.results.append(_run_unit(result, dry_run, pool))

    report.seconds = time.perf_counter() - start
    return report


def _run_unit(result, dry_run=False, pool=None):
    """
    Walk one unit, compile what it answers stale, and report it.

    The unit is one traversal of plan(), cut into batches of COMPILE_BATCH
    items and handed to the pool as it goes, so the listing of the folder
    after a batch runs while that batch compiles. A batch holds both kinds
    of item, the ones the listing settled and the ones whose pyc file has
    to be read, because a listing almost always yields both and splitting
    them only makes the queue of the pool read worse.

    Args:
        result (ScopeResult): The unit of the scope
        dry_run (bool): List the stale sources without compiling them
        pool (ThreadPool): The threads of the run, a unit of a dry run
            has none

    Returns:
        ResultReport: What the unit did
    """
    start = time.perf_counter()
    entry = ResultReport(result.tier, result.target)
    entry.sources = len(result.sources)

    if not result.sources:
        # A unit the scope asked for and did not find, e.g. a distribution
        # that is not installed: the line of the unit is the report of it
        pass
    elif dry_run:
        # A diagnostic, no reason to pay a pool for it
        stale = [file for file in map(ensure, plan(result.sources)) if file is not None]
        stale.sort()
        entry.stale = len(stale)
        for file in stale:
            logger.info(f'  {file}')
    else:
        jobs = [
            pool.start_thread_soon(_work, batch)
            for batch in batched(plan(result.sources), COMPILE_BATCH)
        ]
        for job in jobs:
            stale, compiled, failed, errors = job.get()
            entry.stale += stale
            entry.compiled += compiled
            entry.failed += failed
            for error in errors:
                if len(entry.errors) < ERROR_SAMPLES:
                    entry.errors.append(error)

    entry.seconds = time.perf_counter() - start
    logger.info(entry.line())
    return entry


def _work(batch):
    """
    Compile the sources of one batch that are not fresh.

    Args:
        batch (tuple): The work items of the batch, see fresh.plan()

    Returns:
        tuple[int, int, int, list[str]]: Sources answered stale, written,
            failed, and the first failure messages of the batch
    """
    stale = 0
    compiled = 0
    failed = 0
    errors = []
    for item in batch:
        file = ensure(item)
        if file is None:
            continue
        stale += 1
        error = compile_file(file)
        if not error:
            compiled += 1
            continue
        failed += 1
        if len(errors) < ERROR_SAMPLES:
            errors.append(f'{file}: {error}')
    return stale, compiled, failed, errors
