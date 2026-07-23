from alasio.git.stage.gitadd import GitAdd
from alasio.git.stage.gitcommit import GitCommit
from alasio.git.stage.gitref import GitRef
from alasio.git.stage.gitreset import GitReset


class GitRepo(GitReset, GitCommit, GitAdd, GitRef):
    pass
