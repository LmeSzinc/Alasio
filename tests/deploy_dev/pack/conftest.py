"""
Shared test data: a mock modern full-stack website (python backend + svelte 5 frontend).

The file list is designed to cover every record type produced by PackFull:

- edit: A (added), C (copied, same content as a previous file), D (deleted marker,
  auto-added for folders without __init__.py)
- eol: 0 (LF), 1 (CRLF via .gitattributes), 2 (binary, contains b'\\x00')
- mode: 644 (0) and 755 (1) files, mode comes from the git entry mode
- algo: 0 (raw, small/incompressible files), 1 (lzma, big compressible files)
- empty files (size = 0, sha1 = ''), deep paths, duplicate contents (C)
"""
from alasio.deploy_dev.pack.pack_repo import PackFull
from alasio.git.mock.mock_repo import MockGitRepo

COMMIT = 'c1'

# {path: (content, mode)}
WEBSITE_FILES = {
    '.gitattributes': (
        b'*.py text eol=lf\n*.txt text eol=crlf\n*.bat text eol=crlf\n*.png binary\n',
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
    # same content again -> C (copied), references utils.py -> config.py chain
    'backend/settings.py': (b'HOST = "0.0.0.0"\nPORT = 8000\nDEBUG = False\n', 644),
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
    # windows batch file, CRLF line endings -> eol = 1
    'scripts/run.bat': (b'@echo off\r\npython -m website\r\n', 644),
    'docs/README.md': (b'# Website\n\nModern full-stack site.\n', 644),
}

# Module level singletons shared by all tests in this folder.
# The repo and the encoded pack are treated as read-only test data:
# registering more files or mutating the pack bytes would affect every
# test, create a fresh MockGitRepo instead. Building the pack once here
# avoids re-encoding it in every test.
WEBSITE_REPO = MockGitRepo()
for path, (content, mode) in WEBSITE_FILES.items():
    WEBSITE_REPO.register_file(COMMIT, path, content, mode=mode)
WEBSITE_FULL_PACK = b''.join(PackFull(WEBSITE_REPO, commit=COMMIT).iter_pack_data())
# index pack: header + index section only, no data section
WEBSITE_INDEX_PACK = b''.join(PackFull(WEBSITE_REPO, commit=COMMIT).iter_packidx_data())
