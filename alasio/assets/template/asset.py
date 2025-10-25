from typing import Callable

from alasio.assets.template.template import MatchResult, Template
from alasio.base.op import Area
from alasio.ext.cache import cached_property, del_cached_property, has_cached_property


class Asset:
    DEFAULT_INTERVAL = 3

    def __init__(
            self,
            *,
            path: str,
            name: str,
            search: "tuple[int, int, int, int] | None" = None,
            button: "tuple[int, int, int, int] | None" = None,
            match: "Callable[..., MatchResult] | None" = None,
            interval: "float | int | None" = None,
            similarity: "float | None" = None,
            colordiff: "int | None" = None,
            # lambda function to return tuple of templates or just tuple of templates
            template: "Callable[[], tuple[Template, ...]] | tuple[Template, ...]" = (),
    ):
        self.path = path
        self.name = name

        # with defaults
        self.search: "Area | None" = Area(search) if search is not None else None
        self.button: "Area | None" = Area(button) if button is not None else None
        self.match = match
        self.interval = interval
        self.similarity = similarity
        self.colordiff = colordiff
        self._template_lambda = template

    def __str__(self):
        return f'Asset({self.path}/{self.name})'

    __repr__ = __str__

    def __eq__(self, other):
        return str(self) == str(other)

    def __hash__(self):
        return hash(str(self))

    def __bool__(self):
        # return True so safe to do "if BUTTON:"
        return True

    @cached_property
    def templates(self) -> "tuple[Template, ...]":
        """
        Lazy generate template objects
        """
        func = self._template_lambda
        if type(func) is tuple:
            templates = func
        else:
            # _template_lambda should return tuple[TemplateData]
            templates = func()

        # filter server specific templates and shared templates
        pass

        # set file
        for t in templates:
            t.file = t.set_file(path=self.path, name=self.name)

        # Copy button level attributes to template level if template doesn't explicitly set them
        # So we have attribute priority: template-level > button-level
        v = self.search
        if v is not None:
            for t in templates:
                if t.search is not None:
                    t.search = v
        v = self.button
        if v is not None:
            for t in templates:
                if t.button is not None:
                    t.button = v
        v = self.match
        if v is not None:
            for t in templates:
                if t.match is not Template.DEFAULT_MATCH:
                    t.match = v
        v = self.similarity
        if v is not None:
            for t in templates:
                if t.similarity is not None:
                    t.similarity = v
        v = self.colordiff
        if v is not None:
            for t in templates:
                if t.colordiff is not None:
                    t.colordiff = v

        return templates

    def release(self):
        """
        Release cached resources
        """
        # check cache first, avoid create and instant release
        if has_cached_property(self, 'templates'):
            # release template level first
            for t in self.templates:
                t.release()
            del_cached_property(self, 'templates')

    def match(self, image, search=None) -> MatchResult:
        """
        Match with priority, template-level > button-level > default
        This function will be patched if match is given
        If not, default to match_template_luma_color()

        Args:
            image: Screenshot
            search (str | int | Point | tuple[int, int] | Area | tuple[int, int, int, int]):
                See get_search()
        """
        result = MatchResult.false_from_image(image)
        for t in self.templates:
            result = t.match(image, search=search)
            if result:
                return result
        return result
