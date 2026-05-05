"""
Tests for alasio.git.attr.attr module.

Tests cover:
- GitAttributes._load_content: parsing gitattributes content into patterns
- GitAttributes.apply_files: applying patterns to file lists
"""

from alasio.ext.cache import cached_property
from alasio.git.attr.attr import GitAttributes
from alasio.git.attr.attrfile import (FileAttrs, PatternAny, PatternFileContain, PatternFileName, PatternFileSuffix,
                                      PatternPathPrefix, PatternRegex)


# ==========================================
# Helper: create a fresh GitAttributes with builtin cache pre-set to empty
# ==========================================
def _new_ga():
    """
    Create a new GitAttributes and pre-set _load_builtin cached property
    to None so apply_files won't read the real .gitattributes file.
    """
    ga = GitAttributes()
    cached_property.set(ga, '_load_builtin', None)
    return ga


# ==========================================
# 1. GitAttributes._load_content tests
# ==========================================
class TestGitAttributesLoadContent:
    def test_basic_parsing(self):
        """Basic pathspec + attr lines create patterns"""
        ga = GitAttributes()
        content = """
*.py     text diff=python
*.ts     text
README.md text
"""
        ga._load_content(root="", content=content)
        assert len(ga.patterns) == 3

    def test_comment_lines_skipped(self):
        """Lines starting with # are skipped"""
        ga = GitAttributes()
        content = """
# This is a comment
*.py     text diff=python
# Another comment
*.ts     text
"""
        ga._load_content(root="", content=content)
        assert len(ga.patterns) == 2

    def test_empty_lines_skipped(self):
        """Blank lines are skipped"""
        ga = GitAttributes()
        content = """

*.py     text

*.ts     text

"""
        ga._load_content(root="", content=content)
        assert len(ga.patterns) == 2

    def test_lines_with_only_pathspec_no_attrs_skipped(self):
        """Bare pathspec without attrs produces empty dict, pattern is skipped"""
        ga = GitAttributes()
        content = """
*.py
*.ts     text
"""
        ga._load_content(root="", content=content)
        # "*.py" without attrs -> parse_gitattributes_line returns pathspec='*.py', attrs_dict={}
        # Both pathspec and attrs_dict are checked; empty dict is falsy
        assert len(ga.patterns) == 1

    def test_is_builtin_allows_star_pathspec(self):
        """is_builtin=True does NOT skip pathspec='*'"""
        ga = GitAttributes()
        content = """
* text=auto eol=lf
*.py     text diff=python
"""
        ga._load_content(root="", content=content, is_builtin=True)
        assert len(ga.patterns) == 2
        assert isinstance(ga.patterns[0], PatternAny)

    def test_is_builtin_false_skips_star_pathspec(self):
        """is_builtin=False skips pathspec='*' (repo-level .gitattributes)"""
        ga = GitAttributes()
        content = """
* text=auto eol=lf
*.py     text diff=python
"""
        ga._load_content(root="", content=content, is_builtin=False)
        assert len(ga.patterns) == 1
        assert isinstance(ga.patterns[0], PatternFileSuffix)

    def test_default_is_builtin_skips_star(self):
        """Default is_builtin=False skips pathspec='*'"""
        ga = GitAttributes()
        content = "* text=auto eol=lf"
        ga._load_content(root="", content=content)
        assert len(ga.patterns) == 0

    def test_root_is_passed_to_patterns(self):
        """root parameter is passed through to created patterns"""
        ga = GitAttributes()
        content = """
*.py     text diff=python
*.ts     text
"""
        ga._load_content(root="src/", content=content)
        assert len(ga.patterns) == 2
        for pattern in ga.patterns:
            assert pattern.root == "src/"

    def test_pattern_types_are_correct(self):
        """get_pattern dispatches to correct pattern types"""
        ga = GitAttributes()
        content = """
*.py         text diff=python
*README*     text
package.json text eol=lf
src/*        text
**/*.svelte  text
"""
        ga._load_content(root="", content=content)
        assert len(ga.patterns) == 5
        assert isinstance(ga.patterns[0], PatternFileSuffix)   # *.py
        assert isinstance(ga.patterns[1], PatternFileContain)  # *README*
        assert isinstance(ga.patterns[2], PatternFileName)     # package.json
        assert isinstance(ga.patterns[3], PatternPathPrefix)   # src/*
        assert isinstance(ga.patterns[4], PatternRegex)        # **/*.svelte

    def test_multiple_loads_accumulate_patterns(self):
        """Calling _load_content multiple times appends patterns"""
        ga = GitAttributes()
        ga._load_content(root="", content="*.py text diff=python")
        assert len(ga.patterns) == 1
        ga._load_content(root="src/", content="*.ts text")
        assert len(ga.patterns) == 2
        assert ga.patterns[0].root == ""
        assert ga.patterns[1].root == "src/"

    def test_quoted_pathspec(self):
        """Pathspec with quotes (escaped filenames)"""
        ga = GitAttributes()
        content = '"file name.txt" text eol=lf'
        ga._load_content(root="", content=content)
        assert len(ga.patterns) == 1

    def test_binary_token(self):
        """binary token creates proper attrs (unset text, empty diff, empty merge)"""
        ga = GitAttributes()
        content = "*.png binary"
        ga._load_content(root="", content=content)
        assert len(ga.patterns) == 1
        pattern = ga.patterns[0]
        assert pattern.data["text"] == "unset"
        assert pattern.data["diff"] == ""
        assert pattern.data["merge"] == ""

    def test_unset_attrs(self):
        """Unset attributes with - prefix"""
        ga = GitAttributes()
        content = "*.map text -diff"
        ga._load_content(root="", content=content)
        assert len(ga.patterns) == 1
        pattern = ga.patterns[0]
        assert pattern.data["text"] == "set"
        assert pattern.data["diff"] == ""

    def test_complex_full_content(self):
        """Loading a realistic .gitattributes file content"""
        ga = GitAttributes()
        content = """
# Auto detect text files and perform LF normalization
* text=auto eol=lf

# Source files
*.py     text diff=python
*.ts     text
*.tsx    text

# Binary files
*.db     binary
*.pkl    binary

# Documentation
*.md     text diff=markdown
*README* text

# Configs
package.json text eol=lf
*.yaml       text
*.yml        text
"""
        ga._load_content(root="", content=content, is_builtin=True)
        assert len(ga.patterns) == 11

    def test_empty_content_creates_no_patterns(self):
        """Empty content creates no patterns"""
        ga = GitAttributes()
        ga._load_content(root="", content="")
        assert len(ga.patterns) == 0

    def test_whitespace_only_content_creates_no_patterns(self):
        """Whitespace-only content creates no patterns"""
        ga = GitAttributes()
        ga._load_content(root="", content="   \n  \n  ")
        assert len(ga.patterns) == 0

    def test_only_comments_creates_no_patterns(self):
        """Content with only comments creates no patterns"""
        ga = GitAttributes()
        content = "# comment 1\n# comment 2\n  # indented comment"
        ga._load_content(root="", content=content)
        assert len(ga.patterns) == 0


# ==========================================
# 2. GitAttributes.apply_files tests
# ==========================================
class TestGitAttributesApplyFiles:
    def test_basic_apply_single_file(self):
        """Applying patterns to a single file"""
        ga = _new_ga()
        ga._load_content(root="", content="*.py text diff=python", is_builtin=True)
        files = ga.apply_files(["main.py"])
        assert len(files) == 1
        assert files[0].path == "main.py"
        assert files[0].filename == "main.py"

    def test_basic_apply_multiple_files(self):
        """Applying patterns to multiple files"""
        ga = _new_ga()
        ga._load_content(root="", content="*.py text diff=python\n*.ts text", is_builtin=True)
        files = ga.apply_files(["main.py", "app.ts", "README.md"])
        assert len(files) == 3
        assert files[0].path == "main.py"
        assert files[1].path == "app.ts"
        assert files[2].path == "README.md"

    def test_files_have_correct_filename(self):
        """FileAttrs.filename is correctly extracted"""
        ga = _new_ga()
        ga._load_content(root="", content="*.py text", is_builtin=True)
        files = ga.apply_files(["src/main.py", "backend/api/router.py", "README.md"])
        assert files[0].filename == "main.py"
        assert files[1].filename == "router.py"
        assert files[2].filename == "README.md"

    def test_pattern_file_suffix_matches(self):
        """PatternFileSuffix matches files with correct suffix"""
        ga = _new_ga()
        ga._load_content(root="", content="*.py text diff=python\n*.ts text", is_builtin=True)
        files = ga.apply_files(["main.py", "app.ts", "README.md", "script.sh"])
        # main.py should have the *.py pattern
        assert len(files[0].pending_pattern) == 1
        assert files[0].pending_pattern[0].data["text"] == "set"
        # app.ts should have the *.ts pattern
        assert len(files[1].pending_pattern) == 1
        assert files[1].pending_pattern[0].data["text"] == "set"
        # README.md should have no patterns
        assert len(files[2].pending_pattern) == 0
        # script.sh should have no patterns
        assert len(files[3].pending_pattern) == 0

    def test_pattern_file_name_matches(self):
        """PatternFileName matches exact filenames"""
        ga = _new_ga()
        ga._load_content(root="", content='package.json text eol=lf\n*.py text', is_builtin=True)
        files = ga.apply_files(["package.json", "frontend/package.json", "backend/package.json", "main.py"])
        # All package.json files should match
        assert len(files[0].pending_pattern) == 1
        assert files[0].pending_pattern[0].name == "package.json"
        assert len(files[1].pending_pattern) == 1
        assert len(files[2].pending_pattern) == 1
        # main.py should match *.py
        assert len(files[3].pending_pattern) == 1

    def test_pattern_path_prefix_matches(self):
        """PatternPathPrefix matches files under a directory"""
        ga = _new_ga()
        ga._load_content(root="", content="src/* text", is_builtin=True)
        files = ga.apply_files(["src/main.py", "src/lib/utils.py", "tests/main.py", "frontend/src/app.ts"])
        # src/main.py should match
        assert len(files[0].pending_pattern) == 1
        # src/lib/utils.py should match
        assert len(files[1].pending_pattern) == 1
        # tests/main.py should NOT match
        assert len(files[2].pending_pattern) == 0
        # frontend/src/app.ts should NOT match (prefix "src/" doesn't match "frontend/src/")
        assert len(files[3].pending_pattern) == 0

    def test_pattern_path_prefix_with_root(self):
        """PatternPathPrefix with root prefix combining"""
        ga = _new_ga()
        ga._load_content(root="frontend/", content="src/* text", is_builtin=True)
        files = ga.apply_files(["frontend/src/App.svelte", "backend/src/lib.py", "frontend/static/logo.png"])
        # frontend/src/App.svelte should match (root=frontend/ + prefix=src/)
        assert len(files[0].pending_pattern) == 1
        # backend/src/lib.py should NOT match
        assert len(files[1].pending_pattern) == 0
        # frontend/static/logo.png should NOT match
        assert len(files[2].pending_pattern) == 0

    def test_pattern_regex_matches(self):
        """PatternRegex matches complex patterns"""
        ga = _new_ga()
        ga._load_content(root="", content="**/*.py text diff=python", is_builtin=True)
        files = ga.apply_files(["main.py", "src/main.py", "src/lib/utils.py", "test.txt"])
        # All .py files should match
        assert len(files[0].pending_pattern) == 1
        assert len(files[1].pending_pattern) == 1
        assert len(files[2].pending_pattern) == 1
        # test.txt should not match
        assert len(files[3].pending_pattern) == 0

    def test_pattern_any_builtin_matches_all(self):
        """PatternAny from builtin matches all files"""
        ga = _new_ga()
        ga._load_content(root="", content="* text=auto", is_builtin=True)
        files = ga.apply_files(["main.py", "app.ts", "README.md", "package.json"])
        for file in files:
            assert len(file.pending_pattern) == 1
            assert isinstance(file.pending_pattern[0], PatternAny)
            assert file.pending_pattern[0].data["text"] == "auto"

    def test_star_skipped_in_non_builtin(self):
        """* pathspec is skipped when not builtin"""
        ga = _new_ga()
        # Load builtin first (no real file) to prevent _load_builtin from firing
        ga._load_content(root="", content="* text=auto eol=lf\n*.py text diff=python")
        # Only *.py pattern should be loaded (non-builtin skips *)
        files = ga.apply_files(["main.py", "README.md"])
        # main.py matches *.py
        assert len(files[0].pending_pattern) == 1
        # README.md matches nothing
        assert len(files[1].pending_pattern) == 0

    def test_multiple_patterns_same_file(self):
        """A file can match multiple patterns"""
        ga = _new_ga()
        ga._load_content(root="", content="*.py text diff=python\n**/*.py eol=lf", is_builtin=True)
        files = ga.apply_files(["main.py"])
        # main.py should match both patterns
        assert len(files[0].pending_pattern) == 2

    def test_override_rules_apply(self):
        """Deeper directory patterns override shallower ones"""
        ga = _new_ga()
        ga._load_content(root="", content="*.py text=auto eol=lf diff=default", is_builtin=True)
        ga._load_content(root="src/", content="*.py diff=python", is_builtin=False)
        files = ga.apply_files(["src/main.py", "backend/main.py"])
        # src/main.py: root="" + root="src/" patterns
        # root="src/" is deeper, should override diff=default with diff=python
        assert files[0].attrs_dict == {"text": "auto", "eol": "lf", "diff": "python"}
        # backend/main.py: only root="" pattern
        assert files[1].attrs_dict == {"text": "auto", "eol": "lf", "diff": "default"}

    def test_deeper_patterns_with_suffix(self):
        """Suffix patterns with different root depths"""
        ga = _new_ga()
        ga._load_content(root="", content="*.svelte text=auto", is_builtin=True)
        ga._load_content(root="frontend/", content="*.svelte text=html", is_builtin=False)
        files = ga.apply_files(["frontend/src/App.svelte", "backend/template.svelte"])
        # frontend/src/App.svelte: root="" + root="frontend/" patterns
        # text=html: value is not "auto", so parse_gitattributes_line sets text="set"
        # "set" overrides the shallower "auto"
        assert files[0].attrs_dict == {"text": "set"}
        # backend/template.svelte: only root="" pattern
        assert files[1].attrs_dict == {"text": "auto"}

    def test_override_with_filename_patterns(self):
        """Filename patterns at different depths"""
        ga = _new_ga()
        ga._load_content(root="", content="package.json eol=lf", is_builtin=True)
        ga._load_content(root="frontend/", content="package.json eol=crlf", is_builtin=False)
        files = ga.apply_files(["package.json", "frontend/package.json"])
        # root package.json: eol=lf
        assert files[0].attrs_dict == {"eol": "lf"}
        # frontend/package.json: root="" then root="frontend/" overrides eol
        assert files[1].attrs_dict == {"eol": "crlf"}

    def test_empty_patterns_returns_empty_pending(self):
        """With no patterns loaded, all files have empty pending_pattern"""
        ga = _new_ga()
        files = ga.apply_files(["main.py", "app.ts", "README.md"])
        assert len(files) == 3
        for file in files:
            assert file.pending_pattern == []
            assert file.attrs_dict == {}

    def test_return_type_is_list_of_fileattrs(self):
        """apply_files returns list of FileAttrs"""
        ga = _new_ga()
        ga._load_content(root="", content="*.py text", is_builtin=True)
        files = ga.apply_files(["main.py"])
        assert isinstance(files, list)
        assert isinstance(files[0], FileAttrs)

    def test_input_files_deduplication_assumed(self):
        """Behavior with duplicate file paths (input should be pre-deduplicated by caller)"""
        ga = _new_ga()
        ga._load_content(root="", content="*.py text", is_builtin=True)
        # The method assumes input is already deduplicated
        files = ga.apply_files(["main.py", "main.py", "main.py"])
        assert len(files) == 3
        for file in files:
            assert file.path == "main.py"
            assert len(file.pending_pattern) == 1

    def test_performance_cache_hit_suffix(self):
        """dict_suffix cache works correctly for multiple files with same suffix"""
        ga = _new_ga()
        ga._load_content(root="", content="*.py text diff=python\n*.ts text", is_builtin=True)
        files = ga.apply_files([
            "src/main.py",
            "src/utils.py",
            "backend/api/router.py",
            "frontend/app.ts",
            "frontend/utils.ts",
        ])
        # All .py files should have the *.py pattern
        assert len(files[0].pending_pattern) == 1
        assert len(files[1].pending_pattern) == 1
        assert len(files[2].pending_pattern) == 1
        # All .ts files should have the *.ts pattern
        assert len(files[3].pending_pattern) == 1
        assert len(files[4].pending_pattern) == 1

    def test_performance_cache_hit_name(self):
        """dict_name cache works correctly for multiple files with same name"""
        ga = _new_ga()
        ga._load_content(root="", content="package.json text eol=lf", is_builtin=True)
        files = ga.apply_files([
            "package.json",
            "frontend/package.json",
            "backend/package.json",
            "pypi/package.json",
        ])
        for file in files:
            assert len(file.pending_pattern) == 1
            assert file.pending_pattern[0].data["eol"] == "lf"

    def test_same_name_different_dirs(self):
        """File name patterns match all files with that name regardless of directory"""
        ga = _new_ga()
        ga._load_content(root="", content="Dockerfile text", is_builtin=True)
        files = ga.apply_files([
            "Dockerfile",
            "frontend/Dockerfile",
            "backend/Dockerfile",
            "deploy/Dockerfile",
        ])
        for file in files:
            assert len(file.pending_pattern) == 1
            assert file.pending_pattern[0].data["text"] == "set"

    def test_file_name_pattern_with_root_restriction(self):
        """File name pattern with root only matches under that root"""
        ga = _new_ga()
        ga._load_content(root="frontend/", content="index.ts text", is_builtin=True)
        files = ga.apply_files([
            "frontend/index.ts",
            "frontend/src/index.ts",
            "backend/index.ts",
        ])
        # frontend/index.ts and frontend/src/index.ts should match
        assert len(files[0].pending_pattern) == 1
        assert len(files[1].pending_pattern) == 1
        # backend/index.ts should NOT match
        assert len(files[2].pending_pattern) == 0

    def test_multiple_load_calls(self):
        """load() can be called multiple times to add patterns from different .gitattributes files"""
        ga = _new_ga()
        ga._load_content(root="", content="*.py text=auto eol=lf", is_builtin=True)
        ga._load_content(root="src/", content="*.py diff=python", is_builtin=False)
        ga._load_content(root="frontend/", content="*.ts text eol=crlf", is_builtin=False)
        files = ga.apply_files([
            "src/main.py",
            "backend/main.py",
            "frontend/app.ts",
        ])
        # src/main.py: root="" + root="src/" patterns
        assert files[0].attrs_dict == {"text": "auto", "eol": "lf", "diff": "python"}
        # backend/main.py: root="" pattern only
        assert files[1].attrs_dict == {"text": "auto", "eol": "lf"}
        # frontend/app.ts: root="frontend/" pattern only
        assert files[2].attrs_dict == {"text": "set", "eol": "crlf"}

    def test_unset_in_deeper_directory(self):
        """Deeper patterns can unset attributes set by shallower patterns"""
        ga = _new_ga()
        ga._load_content(root="", content="*.py text=auto eol=lf diff=python merge=union", is_builtin=True)
        ga._load_content(root="src/", content="*.py -diff -merge", is_builtin=False)
        files = ga.apply_files(["src/main.py", "tests/main.py"])
        # src/main.py: deeper pattern sets diff="" and merge=""
        # parse_gitattributes_line produces diff="" for -diff, merge="" for -merge
        # These empty strings override the shallower values
        assert files[0].attrs_dict == {"text": "auto", "eol": "lf", "diff": "", "merge": ""}
        # tests/main.py: only root pattern
        assert files[1].attrs_dict == {"text": "auto", "eol": "lf", "diff": "python", "merge": "union"}

    def test_mixed_pattern_types_integration(self):
        """Integration test: suffix + filename + contain + prefix patterns together"""
        ga = _new_ga()
        ga._load_content(root="", content="""
*.py         text=auto eol=lf diff=python
package.json text eol=lf
*README*     diff=markdown
src/*        eol=crlf
""", is_builtin=True)
        files = ga.apply_files([
            "src/main.py",
            "package.json",
            "README.md",
            "src/README",
            "backend/main.py",
            "doc/README.txt",
        ])
        # src/main.py: *.py + src/* patterns
        assert files[0].attrs_dict == {"text": "auto", "eol": "crlf", "diff": "python"}
        # package.json: package.json pattern
        assert files[1].attrs_dict == {"text": "set", "eol": "lf"}
        # README.md: *README* pattern (no other matches)
        assert files[2].attrs_dict == {"diff": "markdown"}
        # src/README: *README* + src/* patterns
        assert files[3].attrs_dict == {"eol": "crlf", "diff": "markdown"}
        # backend/main.py: *.py pattern only
        assert files[4].attrs_dict == {"text": "auto", "eol": "lf", "diff": "python"}
        # doc/README.txt: *README* pattern only
        assert files[5].attrs_dict == {"diff": "markdown"}

    def test_many_files_large_list(self):
        """apply_files with a large list of files to ensure performance caches work"""
        ga = _new_ga()
        ga._load_content(root="", content="""
*.py    text diff=python
*.ts    text
*.svelte text
*.json  text
""", is_builtin=True)
        # Generate many files with different suffixes
        many_files = []
        for i in range(100):
            many_files.append(f"src/module_{i}.py")
            many_files.append(f"src/component_{i}.ts")
            many_files.append(f"src/page_{i}.svelte")
            many_files.append(f"src/data_{i}.json")
        files = ga.apply_files(many_files)
        assert len(files) == 400
        # Check that suffix cache worked correctly
        for file in files:
            assert len(file.pending_pattern) == 1

    def test_file_with_nested_path(self):
        """Files with deeply nested paths"""
        ga = _new_ga()
        ga._load_content(root="", content="*.py text diff=python", is_builtin=True)
        files = ga.apply_files([
            "a/b/c/d/e/f/g/main.py",
            "very/deeply/nested/path/to/some/script.py",
        ])
        for file in files:
            assert file.filename in ("main.py", "script.py")
            assert len(file.pending_pattern) == 1

    def test_dir_path_without_filename_handling(self):
        """Paths without dot filename (e.g. Dockerfile) still work"""
        ga = _new_ga()
        ga._load_content(root="", content="Dockerfile text\nMakefile text", is_builtin=True)
        files = ga.apply_files(["Dockerfile", "Makefile", "frontend/Dockerfile"])
        assert files[0].filename == "Dockerfile"
        assert files[1].filename == "Makefile"
        assert files[2].filename == "Dockerfile"