"""
Thread pool the encoders of the pack module compress on.

Building a pack compresses thousands of contents: the data section of a full
pack holds every file of the version, and an update pack compresses the records
its diff produced. The compressors (lzma, zstd, the C accelerators called
through ctypes) release the GIL while they run, so their batch runs on a thread
pool instead of the single thread that walks the files, see
doc/2026-09-27_update-pack-from-repo.md section 7.29.

The pool is dedicated to this module. The shared THREAD_POOL runs the blocking
calls of the whole process (device IO, http, cmd): a pack build queues thousands
of compressions on it and would starve them. Its size is the physical core
count of the machine, see get_max_worker: the physical cores are the width that
pays off for a compression batch, the hyperthreads only add contention
(measured 4.06x on 6 physical cores against 4.52x on the 12 logical ones of the
same machine, see the same document section 7.29.2).

The pool blocks a caller while every worker of it is busy -- that is the
backpressure that bounds the jobs in flight, see PackFull._populate_data -- so
the tasks submitted to it must not submit tasks of it themselves: such a task
would wait for a worker that can not come free while it waits.
"""

from alasio.ext.concurrent.processpool import get_max_worker
from alasio.ext.concurrent.threadpool import ThreadPool

# Thread pool of the pack encoders, one worker per physical core of the machine
# (a single worker when the count can not be detected: a narrow pool only costs
# time, while a blind wider one would oversubscribe the machine).
PACK_POOL = ThreadPool(pool_size=get_max_worker() or 1)
