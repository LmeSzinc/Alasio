"""
The COS store and its signature.

    from alasio.deploy_dev.store.cos import CosStore

    store = CosStore(
        bucket='alas2026-1254325529', region='ap-shanghai',
        secret_id=..., secret_key=...,
        prefix='pack/Author_Repo_master',
    )
    listing = store.list()

The signature is a separate module because it is the part every request
depends on and the part a test can pin down without a socket: see sign.py.
"""

from alasio.deploy_dev.store.cos.sign import CosSigner, canonical_string
from alasio.deploy_dev.store.cos.store import CosError, CosStore, normalize_etag

__all__ = [
    'CosError',
    'CosSigner',
    'CosStore',
    'canonical_string',
    'normalize_etag',
]
