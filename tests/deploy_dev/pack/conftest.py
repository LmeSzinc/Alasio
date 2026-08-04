"""
Shared test data: a mock modern full-stack website (python backend + svelte 5 frontend).

The file list is designed to cover every record type produced by PackFull:

- edit: A (added), C (copied, same content as a previous file), D (deleted marker,
  auto-added for folders without __init__.py)
- eol: 0 (LF), 1 (CRLF via .gitattributes), 2 (binary, contains b'\\x00')
- mode: 644 and 755 files (note: PackFull keeps the mode field 0 in full packs,
  it only uses git mode to derive the initial eol)
- algo: 0 (raw, small/incompressible files), 1 (lzma, big compressible files)
- empty files (size = 0, sha1 = ''), deep paths, duplicate contents (C)
"""
import pytest

from alasio.git.mock.mock_repo import MockGitRepo

COMMIT = 'c1'

# {path: (content, mode)}
WEBSITE_FILES = {
    '.gitattributes': (
        b'*.py text eol=lf\n*.txt text eol=crlf\n*.png binary\n',
        644,
    ),
    # python backend
    'backend/__init__.py': (b'', 644),  # empty file
    'backend/main.py': (
        b'import uvicorn\n\nif __name__ == "__main__":\n    uvicorn.run("app:app")\n',
        644,
    ),
    'backend/config.py': (b'HOST = "0.0.0.0"\nPORT = 8000\nDEBUG = False\n', 644),
    # same content as config.py -> C (copied)
    'backend/utils.py': (b'HOST = "0.0.0.0"\nPORT = 8000\nDEBUG = False\n', 644),
    # CRLF text -> eol = 1
    'backend/requirements.txt': (b'fastapi==0.111.0\r\nuvicorn==0.30.1\r\n', 644),
    'backend/api/__init__.py': (b'from .routes import router\n', 644),
    'backend/api/routes.py': (
        b'from starlette.routing import Route\n\nasync def index(request):\n    return {}\n',
        644,
    ),
    # binary content (contains b'\\x00') -> eol = 2
    'backend/static/logo.png': (bytes(range(256)) * 100, 644),
    # backend/tools/ has no __init__.py -> auto D marker added
    'backend/tools/helper.py': (b'def helper():\n    return 42\n', 644),
    # svelte 5 frontend
    'frontend/package.json': (b'{\n  "name": "website",\n  "type": "module"\n}\n', 644),
    'frontend/tsconfig.json': (b'{\n  "compilerOptions": {\n    "strict": true\n  }\n}\n', 644),
    'frontend/src/App.svelte': (
        b'<script lang="ts">\n  let count = $state(0);\n</script>\n\n'
        b'<button onclick={() => count++}>{count}</button>\n',
        644,
    ),
    # same content as App.svelte -> C (copied)
    'frontend/src/lib/Button.svelte': (
        b'<script lang="ts">\n  let count = $state(0);\n</script>\n\n'
        b'<button onclick={() => count++}>{count}</button>\n',
        644,
    ),
    # big & compressible -> algo = 1 (lzma)
    'frontend/src/lib/styles.css': (b'.btn {\n  color: red;\n  padding: 1rem;\n}\n' * 3000, 644),
    'frontend/src/routes/+page.svelte': (
        b'<script lang="ts">\n  import App from "$lib/App.svelte";\n</script>\n\n<App />\n',
        644,
    ),
    # executable scripts (mode 755)
    'scripts/deploy.sh': (b'#!/bin/sh\nset -e\necho "deploy"\n', 755),
    # same content as deploy.sh -> C (copied)
    'scripts/run.sh': (b'#!/bin/sh\nset -e\necho "deploy"\n', 755),
    'docs/README.md': (b'# Website\n\nModern full-stack site.\n', 644),
}


@pytest.fixture
def website_repo():
    """
    MockGitRepo containing a modern full-stack website file list.

    Returns:
        MockGitRepo: Repo with all files registered under COMMIT
    """
    repo = MockGitRepo()
    for path, (content, mode) in WEBSITE_FILES.items():
        repo.register_file(COMMIT, path, content, mode=mode)
    return repo
