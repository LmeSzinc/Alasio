from datetime import timedelta

import typing_extensions as e

import alasio.config.alasio.group_base as a
from alasio.base.timer import getnow


class _FutureMixinBase(a.GroupBase):
    def limit_future(self, delta):
        now = getnow()
        if self.NextRun - now > delta:
            self.NextRun = now


class Future12hMixin(_FutureMixinBase):
    def post_edit(self, old: e.Self, edits):
        if 'NextRun' in edits:
            self.limit_future(timedelta(hours=12, seconds=-1))


class Future24hMixin(_FutureMixinBase):
    def post_edit(self, old: e.Self, edits):
        if 'NextRun' in edits:
            self.limit_future(timedelta(hours=24, seconds=-1))


class Future1monthMixin(_FutureMixinBase):
    def post_edit(self, old: e.Self, edits):
        if 'NextRun' in edits:
            self.limit_future(timedelta(days=31, seconds=-1))


class Future1weekMixin(_FutureMixinBase):
    def post_edit(self, old: e.Self, edits):
        if 'NextRun' in edits:
            self.limit_future(timedelta(days=7, seconds=-1))
