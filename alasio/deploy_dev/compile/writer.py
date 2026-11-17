"""
Write the pins of a resolution into the requirements file a client installs.

The format is the one pip-compile and uv write, and the one the pack server and
the pack_deps tool already read (alasio.deploy.simple_pip.parse_req):

```
name==version
    # via
    #   parent1
    #   parent2
    # from https://pypi.org/simple
```

- a pin is one line, ``name==version``, the names sorted by name;
- ``# via`` is who asked for the pin: the project itself
  (``<project> (pyproject.toml)``) or the distributions that require it, which
  is the annotation the lock is read back with;
- ``# from`` is the index the pinned file comes from, which is what the pack
  server fetches the wheel of the pack from and what ``pack_deps`` reads;
- an index that is not the primary one is declared once, at the top of the
  file, as ``--extra-index-url``: a client that installs the lock reads the
  same indexes the compiler did. The primary index is not written as
  ``--index-url``, because a url in the file overrides the mirror of the pip
  configuration of the user, while the extra index has to stay.

The header is the text of the caller: it says how the file is generated, which
is a property of the project rather than of the compiler.

Usage:
    from alasio.deploy_dev.compile.writer import requirements_text

    text = requirements_text('mymod', resolution, indexes, header='# generated')
"""

__all__ = ['requirements_text']


def requirements_text(project, resolution, indexes=(), header=''):
    """
    Render a resolution as the content of a requirements file.

    Args:
        project (str): PEP 503 normalized name of the project, the annotation
            of a pin the project declares itself
        resolution (Resolution): The pins, their sources and their parents
        indexes (Iterable[Index]): The indexes of the compile, the primary
            first, used for the text of ``--extra-index-url`` and ``# from``.
            Defaults to (), no index to declare
        header (str): Text written before the pins, its lines starting with
            '#'. Defaults to '', no header

    Returns:
        str: Content of the requirements file, ending with a newline
    """
    indexes = list(indexes)
    text_of = {index.url: index.text for index in indexes}
    used = {resolution.sources.get(name, '') for name in resolution.pins}
    extra = [
        index for index in indexes
        if not index.primary and index.url in used
    ]

    lines = []
    if header:
        lines.extend(header.rstrip('\n').split('\n'))
        lines.append('')
    for index in extra:
        lines.append(f'--extra-index-url {index.text}')
    if extra:
        lines.append('')

    for name in sorted(resolution.pins):
        lines.append(f'{name}=={resolution.pins[name]}')
        parents = _parents_of(project, resolution.parents.get(name, ()))
        if len(parents) == 1:
            lines.append(f'    # via {parents[0]}')
        elif parents:
            lines.append('    # via')
            lines.extend(f'    #   {parent}' for parent in parents)
        source = resolution.sources.get(name, '')
        if source:
            lines.append(f'    # from {text_of.get(source, source)}')
    return '\n'.join(lines) + '\n'


def _parents_of(project, parents):
    """
    The parents of a pin, as the ``# via`` annotation spells them.

    The project itself comes first, as ``<name> (pyproject.toml)``: a pin the
    project declares is the requirement the lock exists for, while the others
    are the distributions that pulled it in. uv writes the order this way, and
    the lock of a project is compared line by line with the one uv produces,
    so the annotation keeps it. The distributions are sorted after it.

    Args:
        project (str): PEP 503 normalized name of the project
        parents (Iterable[str]): The origins of the requirements of the pin

    Returns:
        list[str]: The parents, the project first
    """
    own = []
    others = []
    for parent in parents:
        if parent == 'pyproject.toml':
            own.append(f'{project} (pyproject.toml)')
        else:
            others.append(parent)
    return own + sorted(others)
