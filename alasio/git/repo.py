from alasio.git.stage.gitadd import GitAdd
from alasio.git.stage.gitcommit import GitCommit
from alasio.git.stage.gitreset import GitReset
from alasio.git.stage.gittag import GitTag


class GitRepo(GitReset, GitCommit, GitAdd, GitTag):
    pass
