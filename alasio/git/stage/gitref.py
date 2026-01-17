import os
from typing import Literal

from msgspec import Struct

from alasio.ext.cache import cached_property
from alasio.ext.path.atomic import atomic_read_bytes, atomic_remove, atomic_write
from alasio.ext.path.calc import joinnormpath, subpath_to, to_posix
from alasio.ext.path.iter import iter_files
from alasio.git.stage.base import GitRepoBase
from alasio.logger import logger


class LooseRef(Struct):
    sha1: str = ''
    ref: str = ''


def parse_loose_ref(content):
    """
    Args:
        content (bytes): commit sha1 or ref like "ref: refs/heads/dev"

    Returns:
        LooseRef:

    Raises:
        ValueError:
    """
    content = content.strip()
    if content.startswith(b'ref: '):
        try:
            ref = content[5:].decode('utf-8')
        except UnicodeDecodeError as e:
            raise ValueError(e)
        return LooseRef(ref=ref)
    if len(content) == 40:
        try:
            sha = content.decode('ascii')
        except UnicodeDecodeError as e:
            raise ValueError(e)
        return LooseRef(sha1=sha)
    raise ValueError(f'Invalid loose ref: {content}')


def parse_packed_refs(content):
    """
    Args:
        content (bytes):

    Returns:
        dict[str, str]: ref, sha1
            for tags, sha1 can be commit sha1 or tag sha1
    """
    out = {}
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        # ignore comment and header labels
        if line.startswith(b'#'):
            continue
        # ignore peeled tags
        if line.startswith(b'^'):
            continue

        sha, sep, ref = line.partition(b' ')
        if not sep:
            continue
        # validate sha1 and ref
        try:
            sha = sha.decode('ascii')
        except UnicodeDecodeError:
            continue
        if not len(sha) == 40:
            continue
        try:
            ref = ref.decode('utf-8')
        except UnicodeDecodeError:
            continue
        out[ref] = sha

    return out


class GitRef(GitRepoBase):
    @cached_property
    def _packed_refs(self):
        """
        Returns:
            dict[str, str]: ref, sha1
                for tags, sha1 can be commit sha1 or tag sha1
        """
        file = joinnormpath(self.path, '.git/packed-refs')
        try:
            content = atomic_read_bytes(file)
        except FileNotFoundError:
            return {}
        return parse_packed_refs(content)

    @cached_property
    def ref_all(self):
        out = self._packed_refs
        root = joinnormpath(self.path, '.git')

        # iter loose refs
        unsolved = {}
        for file in iter_files(joinnormpath(self.path, '.git/refs'), recursive=True):
            try:
                content = atomic_read_bytes(file)
                loose = parse_loose_ref(content)
            except FileNotFoundError:
                continue
            except ValueError as e:
                logger.error(f'GitRef error, {e}, at file "{file}"')
                continue
            path = to_posix(subpath_to(file, root))
            if loose.sha1:
                # loose refs can override packed refs
                out[path] = loose.sha1
            elif loose.ref:
                unsolved[path] = loose.ref
            else:
                # this shouldn't happen
                logger.error(f'GitRef error, no sha1 nor ref, at file {file}')

        # solve refs
        if unsolved:
            while 1:
                new = {}
                for path, ref in unsolved.items():
                    if ref in out:
                        # reference an existing ref
                        out[path] = out[ref]
                    else:
                        # unsolved, try next round
                        new[path] = ref
                if not new:
                    break
                if len(new) == len(unsolved):
                    logger.error(f'GitRef error, failed to solve ref, remains: {new}')
                    break
                unsolved = new

        return out

    def ref_get(self, ref):
        """
        Args:
            ref (str): E.g. refs/heads/master, HEAD

        Returns:
            str: sha1, or empty string ""
        """
        file = joinnormpath(self.path, f'.git/{ref}')
        try:
            content = atomic_read_bytes(file)
            loose = parse_loose_ref(content)
        except FileNotFoundError:
            # no loose ref. lookup packed refs
            return self._packed_refs.get(ref, '')
        except ValueError as e:
            logger.error(f'GitRef error, {e}, at file "{file}"')
            return ''
        if loose.sha1:
            return loose.sha1
        if loose.ref:
            # solve recursively, recursion is ok because refs won't get over 1000 depth
            return self.ref_get(loose.ref)
        # this shouldn't happen
        logger.error(f'GitRef error, no sha1 nor ref, at file {file}')

    def head_get(self, head: Literal['HEAD', 'ORIG_HEAD'] = None):
        """
        Get current git HEAD or ORIG_HEAD

        Args:
            head: None to get head with fallback, or input specific head

        Returns:
            str: sha1, or empty string ""
        """
        if head is None:
            heads = ['HEAD', 'ORIG_HEAD']
        else:
            heads = [head]

        for head in heads:
            file = joinnormpath(self.path, f'.git/{head}')
            try:
                content = atomic_read_bytes(file)
                loose = parse_loose_ref(content)
            except FileNotFoundError:
                # no head file, try another
                continue
            except ValueError as e:
                logger.error(f'GitRef.head_get error, {e}, at file "{file}"')
                return ''
            if loose.sha1:
                return loose.sha1
            if loose.ref:
                return self.ref_get(loose.ref)

        return ''

    def ref_set(self, ref, target_sha1='', target_ref=''):
        """
        Args:
            ref (str): E.g. refs/heads/master, HEAD
            target_sha1 (str):
            target_ref (str): E.g. refs/remotes/origin/master
        """
        if target_sha1:
            content = f'{target_sha1}\n'
        elif target_ref:
            content = f'ref: {target_ref}\n'
        else:
            return

        file = joinnormpath(self.path, f'.git/{ref}')
        atomic_write(file, content)

    def ref_del(self, ref):
        """
        Args:
            ref (str):

        Returns:
            bool: If removed
        """
        file = joinnormpath(self.path, f'.git/{ref}')
        return atomic_remove(file)

    def ref_exist(self, ref):
        """
        Args:
            ref (str):

        Returns:
            bool:
        """
        file = joinnormpath(self.path, f'.git/{ref}')
        return os.path.exists(file)
