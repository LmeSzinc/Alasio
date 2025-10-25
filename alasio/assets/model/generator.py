from alasio.assets.model.name import to_asset_name
from alasio.assets.model.parser import AssetParser, MetaAsset, MetaTemplate
from alasio.assets.template import Template
from alasio.base.image.color import get_color
from alasio.base.image.draw import get_bbox
from alasio.base.image.imfile import ImageBroken, crop, image_load, image_save, image_size
from alasio.base.op import Area, RGB, random_id
from alasio.config.entry.const import DICT_MOD_ENTRY, ModEntryInfo
from alasio.ext import env
from alasio.ext.cache import cached_property
from alasio.ext.codegen import CodeGen, ReprWrapper
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import atomic_remove
from alasio.ext.path.calc import get_rootstem, to_posix
from alasio.logger import logger


def normalize_similarity(v: "float | None") -> "float | None":
    """
    Limit similarity in 0 to 1 and remove default
    """
    if v is None:
        return None
    if v == Template.DEFAULT_SIMILARITY:
        return None
    if v < 0:
        return 0.
    if v > 1:
        return 1.
    return float(v)


def normalize_colordiff(v: "int | None") -> "int | None":
    """
    Limit similarity in 0 to 255 and remove default
    """
    if v is None:
        return None
    if v == Template.DEFAULT_COLORDIFF:
        return None
    if v < 0:
        return 0
    if v > 255:
        return 255
    return int(v)


def normalize_match(v: "str | None | callable") -> "str | None":
    """
    Convert match function to name like "Template.match" and remove default
    """
    if type(v) is str:
        if v == 'Template.match':
            return None
        if v == Template.DEFAULT_MATCH.__qualname__:
            return None
        return v
    if callable(v):
        if v == Template.match:
            return None
        if v == Template.DEFAULT_MATCH:
            return None
        name = v.__qualname__
        if name == 'Template.match':
            return None
        if name == Template.DEFAULT_MATCH.__qualname__:
            return None
        return name
    return None


def normalize_search(t: Template, v: Area) -> "Area | None":
    if v is None:
        return None
    search = t.area.outset(Template.DEFAULT_SEARCH_OUTSET)
    if t.search == search:
        return None
    return v.as_int()


def normalize_button(t: Template, v: Area) -> "Area | None":
    if v is None:
        return None
    if t.area == v:
        return None
    return v.as_int()


class AssetGenerator:
    def __init__(self, entry: ModEntryInfo, path: str, gitadd=None):
        """
        Args:
            entry:
            path: Relative path from mod root to asset folder
            gitadd (GitAdd): Input a GitAdd object to track the generated files
        """
        self.entry = entry
        self.path = to_posix(path)
        self.gitadd = gitadd
        self.root = PathStr.new(entry.root)
        self.file = self.root / path / 'asset.py'

    def _template_remove(self, template: Template):
        logger.info(f'Delete template: {template}')
        file = self.root / template.file
        atomic_remove(file)

    def _template_generate(self, template: MetaTemplate, image=None):
        """
        Args:
            template:
            image: A cropped image

        Returns:
            bool: If success
        """
        file = self.root / template.file
        source = self.root / self.path / template.meta_source
        logger.info(f'Generate template: {file}')

        if image is None:
            try:
                image = image_load(source, area=template.area)
            except FileNotFoundError:
                return False
            except ImageBroken as e:
                logger.error(e)
                return False

        image_save(file, image, skip_same=True)
        if self.gitadd:
            self.gitadd.stage_add(file)
        return True

    def _validate_template(self, t: MetaTemplate) -> "MetaTemplate | None":
        """
        Normalize template object
        """
        # template must have source
        if not t.meta_source:
            return None

        t.area = t.area.as_int()
        t.color = t.color.as_uint8()
        t.search = normalize_search(t, t.search)
        t.button = normalize_button(t, t.button)

        # server will be validated in _validate_asset()
        # frame will be hard set
        # file will be hard set
        t.match = normalize_match(t.match)
        t.similarity = normalize_similarity(t.similarity)
        t.colordiff = normalize_colordiff(t.colordiff)
        return t

    def _validate_asset(self, a: MetaAsset) -> MetaAsset:
        """
        Normalize asset object and its meta_templates
        """
        # inject path name
        a.path = self.path

        if a.search:
            a.search = a.search.as_int()
        if a.button:
            a.button = a.button.as_int()
        a.match = normalize_match(a.match)
        a.similarity = normalize_similarity(a.similarity)
        a.colordiff = normalize_colordiff(a.colordiff)

        # filter templates
        templates = []
        dict_count = {lang: 0 for lang in self.entry.iter_asset_lang()}
        for t in a.meta_templates:
            # inject path name
            t.set_file(path=self.path, name=a.name)
            # check lang
            if t.lang not in dict_count:
                # invalid lang
                logger.info(f'Template has invalid lang "{t.lang}", {t}')
                self._template_remove(t)
                continue
            # check source
            if not t.meta_source:
                logger.warning(f'Template does not have source: {t}')
                continue
            # check frame
            frame = dict_count[t.lang] + 1
            dict_count[t.lang] = frame
            if frame != t.frame:
                logger.info(f'Template has invalid frame={t.frame}, expect frame={frame}, {t}')
                # incorrect frame, fix it
                self._template_remove(t)
                t.frame = frame
                t.set_file(path=self.path, name=a.name)
                self._template_generate(t)
            # validate
            t = self._validate_template(t)
            if t:
                templates.append(t)

        a.meta_templates = templates
        return a

    @cached_property
    def assets(self) -> "dict[str, MetaAsset]":
        """
        Read asset.py, parse and validate into a list of MetaAsset

        Returns:
            dict[str, MetaAsset]: key: asset name
        """
        try:
            code = self.file.atomic_read_text()
        except FileNotFoundError:
            return {}
        assets = AssetParser(code).parse_assets()
        out = {}
        for a in assets:
            a = self._validate_asset(a)
            if a is not None:
                out[a.name] = a
        return out

    def _template_codegen(self, gen: CodeGen, template: MetaTemplate):
        with gen.Object('Template'):
            # lang='en', frame=2, area=(100, 100, 200, 200), color=(234, 245, 248),
            attrs = []
            if template.lang:
                attrs.append(f'lang={repr(template.lang)}')
            if template.frame != 1:
                attrs.append(f'frame={repr(template.frame)}')
            attrs.append(f'area={repr(template.area)}')
            attrs.append(f'color={repr(template.color)}')
            row = ', '.join(attrs)
            gen.add(row)

            # additional properties
            # button=(200, 200, 300, 300),
            if template.search is not None:
                gen.Var(name='search', value=template.search)
            if template.button is not None:
                gen.Var(name='button', value=template.button)
            if template.match is not None:
                gen.Var(name='match', value=ReprWrapper(template.match))
            if template.similarity is not None:
                gen.Var(name='similarity', value=template.similarity)
            if template.colordiff is not None:
                gen.Var(name='colordiff', value=template.colordiff)
            if template.meta_source:
                gen.Comment(f"source='{template.meta_source}'")

    def asset_codegen(self):
        """
        Re-generate asset.py
        """
        if not self.assets:
            self.file.atomic_remove()
            return

        gen = CodeGen()
        gen.RawImport("""
        from alasio.assets.template import Asset, Template
        """)
        gen.CommentCodeGen('dev_tools.button_extract')
        gen.Var(name='_path_', value=self.path)
        gen.Empty()

        for asset in self.assets.values():
            if asset.meta_asset_doc:
                for line in asset.meta_asset_doc.split('\n'):
                    gen.Comment(line)
            with gen.Object(name=asset.name, cls='Asset'):
                gen.add(f'path=_path_, name={repr(asset.name)}')
                # additional properties
                # button=(200, 200, 300, 300),
                if asset.search is not None:
                    gen.Var(name='search', value=asset.search)
                if asset.button is not None:
                    gen.Var(name='button', value=asset.button)
                if asset.match is not None:
                    gen.Var(name='match', value=ReprWrapper(asset.match))
                if asset.similarity is not None:
                    gen.Var(name='similarity', value=asset.similarity)
                if asset.colordiff is not None:
                    gen.Var(name='colordiff', value=asset.colordiff)

                # template=lambda: (
                if asset.meta_templates:
                    with gen.tab(prefix='template=lambda: (', suffix=')', line_ending=','):
                        for t in asset.meta_templates:
                            self._template_codegen(gen, t)
                else:
                    gen.Var(name='template', value=())

                # ref
                if asset.meta_ref:
                    for ref in asset.meta_ref:
                        gen.Comment(f"ref='{ref}'")

        op = gen.write(self.file, gitadd=self.gitadd)
        if op:
            logger.info(f'Write assets code: {self.file}')

    def add_source_as_asset(self, source, override=False):
        """
        Args:
            source (str): Source filename, e.g. "~BATTLE_PREPARATION.png"
            override (bool): True to override existing asset, false to create with random suffix
        """
        if not source.startswith('~'):
            raise ValueError(f'Source file should startswith "~", got "{source}"')
        source_file = self.root / self.path / source
        image = image_load(source_file)
        area = get_bbox(image)
        color = RGB(get_color(image, area)).as_uint8()

        # get name
        name = to_asset_name(get_rootstem(source))
        assets = self.assets
        if not override and name in assets:
            name = f'{name}_{random_id()}'
        # create info
        template = MetaTemplate(area=area, color=color)
        template.meta_source = source
        asset = MetaAsset(path=self.path, name=name, template=(template,))
        asset.meta_templates = [template]
        self._validate_asset(asset)
        logger.info(f'New asset: {asset}')

        # create template file
        if Area.from_size(image_size(image)) == area:
            # not a mask image
            self._template_generate(template, image=image)
        else:
            # save cropped mask image
            im = crop(image, area, copy=False)
            self._template_generate(template, image=im)

        assets[asset.name] = asset
        return True

    def del_asset(self, asset_name):
        """
        Delete an asset and all its templates

        Args:
            asset_name (str): Name of the asset to delete

        Returns:
            bool: True if asset was deleted
        """
        assets = self.assets
        if asset_name not in assets:
            return False

        logger.info(f'Deleted asset: {asset_name}')
        asset = assets.pop(asset_name, None)
        # Remove all template files
        if asset:
            for t in asset.meta_templates:
                self._template_remove(t)

        return True


if __name__ == '__main__':
    _entry = DICT_MOD_ENTRY['example_mod'].copy()
    _entry.root = env.PROJECT_ROOT.joinpath('ExampleMod')
    self = AssetGenerator(_entry, 'assets/combat')
    self.asset_codegen()
    self.add_source_as_asset('~map prepare cn hard.png')
    self.asset_codegen()
