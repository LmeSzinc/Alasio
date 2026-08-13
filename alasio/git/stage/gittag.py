from alasio.ext.cache import cached_property
from alasio.git.file.gitobject import GitObjectManager
from alasio.git.obj.objtag import TagObject
from alasio.git.stage.gitref import GitRef


class GitTag(GitObjectManager, GitRef):
    @cached_property
    def tags(self):
        """
        Get all tag names in the repository.

        Returns:
            list[str]: Tag names
        """
        return [ref[len('refs/tags/'):] for ref in self.ref_all if ref.startswith('refs/tags/')]

    def tag_get(self, name):
        """
        Get the TagObject of a tag.

        For a lightweight tag, the tagger info is built from the committer
        attributes of the commit it points to.

        Args:
            name (str): Tag name, e.g. "v2020.04.08"

        Returns:
            TagObject | None: None if the tag does not exist, or the tag
                points to a blob or a tree
        """
        sha1 = self.ref_get(f'refs/tags/{name}')
        if not sha1:
            return None
        obj = self.cat(sha1)
        if obj.type == 4:
            # annotated tag
            return obj.decoded
        if obj.type == 1:
            # lightweight tag, build tagger info from the commit
            commit = obj.decoded
            return TagObject(
                object=sha1,
                type='commit',
                tag=name,
                tagger_name=commit.committer_name,
                tagger_email=commit.committer_email,
                tagger_time=commit.committer_time,
                tagger_tz=commit.committer_tz,
                message='',
            )
        # tag points to a blob or a tree
        return None
