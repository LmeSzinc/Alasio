"""
Run a warmup: scan the scope of every tier, then compile what is stale.

The two phases are kept apart on purpose. The scan is IO bound, it lists
every folder of the scope once and reads the header of the pyc files that
may be stale; the compilation is CPU bound. Interleaving them makes the
disk seek back and forth between a folder it just read and the folder it
is about to write, and it makes a run that has nothing to do pay the
compilation pass anyway.

    phase A  for every tier, stale_sources(): the files to compile
    phase B  for every stale file, smallest first: compile_file()

A run where everything is cached stops after phase A, which is the common
case: the second start of a release reads about five thousand headers and
writes nothing.

The compilation order is by size, so an interrupted run has warmed the
widest set of modules: the small files hold most of the modules, the few
big ones, e.g. a data module of several hundred kilobytes, hold the ones
that are least likely to be imported at startup.

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
source and split freely. The listing of the scope therefore overlaps the
compilations instead of waiting for them to end.

One pool serves the whole run. The threads are created once, and a tier
that ends on a partial batch leaves its threads to the next tier instead
of letting them idle at every boundary. A tier is waited for at its end,
which the measurement shows costs nothing (3.917 s against 3.927 s on a
cold tree of 2400 sources) and keeps the counters of a tier exact.

The report is logged, one line per tier: the module has no channel of its
own to write to, so a caller that wants the lines collected, or muted,
sets that on the logger, see __main__.
"""
import time

from alasio.backport.batch import batched
from alasio.deploy.pyc_warm.compile import compile_file
from alasio.deploy.pyc_warm.fresh import ensure, plan, stale_sources
from alasio.deploy.pyc_warm.scope import mod_sources, site_packages_dirs, site_packages_tiers
from alasio.ext.concurrent.threadpool import ThreadPool
from alasio.ext.path.calc import get_name
from alasio.logger import logger

# How many failure messages one tier keeps for the report, the count of
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


class TierReport:
    """
    What one tier did.

    Attributes:
        name (str): Name of the tier, e.g. "pack" or "mod E:/release"
        folder (str): Folder the tier read, '' for a mod tree
        sources (int): Number of sources the tier listed
        stale (int): Number of sources whose cache had to be compiled
        compiled (int): Number of pyc files written
        failed (int): Number of sources that could not be compiled
        seconds (float): Time of the scan and the compilation of the tier
        errors (list[str]): The first failure messages of the tier
    """

    def __init__(self, name, folder=''):
        """
        Args:
            name (str): Name of the tier
            folder (str): Folder the tier reads
        """
        self.name = name
        self.folder = folder
        self.sources = 0
        self.stale = 0
        self.compiled = 0
        self.failed = 0
        self.seconds = 0.0
        self.errors = []

    def line(self):
        """
        Returns:
            str: The tier as one line of the report, e.g.
                "pack    : 1402 sources, 102 stale, 102 compiled, 0 failed, 1.12s"
        """
        return (
            f'{self.name:<8}: {self.sources} sources, {self.stale} stale, '
            f'{self.compiled} compiled, {self.failed} failed, {self.seconds:.2f}s'
        )

    def to_dict(self):
        """
        Returns:
            dict: The tier as a plain dict, the JSON form of the report
        """
        return {
            'name': self.name,
            'folder': self.folder,
            'sources': self.sources,
            'stale': self.stale,
            'compiled': self.compiled,
            'failed': self.failed,
            'seconds': round(self.seconds, 3),
            'errors': self.errors,
        }


class Report:
    """
    What one warmup run did.

    Attributes:
        tiers (list[TierReport]): The tiers in the order they ran
        unresolved (list[str]): Names the scope asked for and did not
            find, e.g. a distribution that is not installed
        seconds (float): Time of the whole run
    """

    def __init__(self):
        self.tiers = []
        self.unresolved = []
        self.seconds = 0.0

    @property
    def compiled(self):
        """
        Returns:
            int: Number of pyc files written by the run
        """
        return sum(tier.compiled for tier in self.tiers)

    @property
    def failed(self):
        """
        Returns:
            int: Number of sources the run could not compile
        """
        return sum(tier.failed for tier in self.tiers)

    @property
    def stale(self):
        """
        Returns:
            int: Number of sources the run had to compile
        """
        return sum(tier.stale for tier in self.tiers)

    def to_dict(self):
        """
        Returns:
            dict: The run as a plain dict, the JSON form of the report
        """
        return {
            'tiers': [tier.to_dict() for tier in self.tiers],
            'unresolved': self.unresolved,
            'compiled': self.compiled,
            'failed': self.failed,
            'stale': self.stale,
            'seconds': round(self.seconds, 3),
        }


def warm(site_packages=None, mod_roots=(), requirements_file=None, dry_run=False):
    """
    Warm the bytecode cache of an environment and the trees of the mods.

    The tiers run in the order of the scope module: the pack distributions,
    the common ones, the other pins, then the mods. A tier is scanned and
    compiled before the next one is scanned, so a run that is interrupted
    has finished the tiers that matter, see scope.py.

    The lines of the report go to the logger at INFO, one for a tier and one
    for every source of a dry run.

    Args:
        site_packages (list[str]): Posix paths of the site-packages
            folders to warm. Defaults to None, the folders of the
            running interpreter, see site_packages_dirs()
        mod_roots (list[str]): Posix paths of the mod trees. Defaults to
            an empty tuple, no mod is warmed
        requirements_file (str): Path of the requirements file of the
            mod, the pins of the lock tier. Defaults to None, no lock tier
        dry_run (bool): List the sources to compile without compiling
            them. Defaults to False

    Returns:
        Report: What the run did
    """
    start = time.perf_counter()
    report = Report()

    if site_packages is None:
        site_packages = site_packages_dirs()
    tiers = []
    for folder in site_packages:
        for name, scope in site_packages_tiers(folder, requirements_file):
            tiers.append((name, scope.sources, folder))
            report.unresolved += scope.unresolved
    for root in mod_roots:
        # The name of the tree tells the mods apart in the report
        tiers.append((f'mod {get_name(root)}', mod_sources(root), root))

    if dry_run:
        # A diagnostic, no reason to pay a pool for it
        for name, sources, folder in tiers:
            report.tiers.append(_run_tier(name, sources, folder, dry_run))
    else:
        pool = ThreadPool(pool_size=WORKERS)
        for name, sources, folder in tiers:
            report.tiers.append(_run_tier(name, sources, folder, dry_run, pool))

    report.seconds = time.perf_counter() - start
    return report


def _run_tier(name, sources, folder='', dry_run=False, pool=None):
    """
    Walk one tier, compile what it answers stale, and report it.

    The tier is one traversal of plan(), cut into batches of
    COMPILE_BATCH items and handed to the pool as it goes, so the listing
    of the folder after a batch runs while that batch compiles. A batch
    holds both kinds of item, the ones the listing settled and the ones
    whose pyc file has to be read, because a listing almost always yields
    both and splitting them only makes the queue of the pool read worse.

    Args:
        name (str): Name of the tier
        sources (list[str]): Paths of the sources of the tier
        folder (str): Folder the tier reads
        dry_run (bool): List the stale sources without compiling them
        pool (ThreadPool): The threads of the run, a tier of a dry run
            has none

    Returns:
        TierReport: What the tier did
    """
    start = time.perf_counter()
    tier = TierReport(name, folder)
    tier.sources = len(sources)

    if dry_run:
        # A diagnostic, no reason to pay a pool for it
        stale = stale_sources(sources)
        tier.stale = len(stale)
        for file in stale:
            logger.info(f'  {file}')
    else:
        jobs = [
            pool.start_thread_soon(_work, batch)
            for batch in batched(plan(sources), COMPILE_BATCH)
        ]
        for job in jobs:
            stale, compiled, failed, errors = job.get()
            tier.stale += stale
            tier.compiled += compiled
            tier.failed += failed
            for error in errors:
                if len(tier.errors) < ERROR_SAMPLES:
                    tier.errors.append(error)

    tier.seconds = time.perf_counter() - start
    logger.info(tier.line())
    return tier


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
