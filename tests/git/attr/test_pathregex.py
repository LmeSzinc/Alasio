import re

import pytest

from alasio.git.attr.pathregex import (_convert_normal_chunk, _split_pathspec, gitattributes_to_regex)


# ==========================================
# 1. 底层辅助函数 _split_pathspec 测试
# ==========================================
class TestSplitPathspec:
    def test_normal_split(self):
        """测试正常路径切割"""
        assert _split_pathspec("a/b/c") == ["a", "b", "c"]

    def test_multiple_slashes(self):
        """测试连续斜线（空块）"""
        assert _split_pathspec("a//b") == ["a", "", "b"]

    def test_escaped_slash(self):
        """测试被转义的斜线不应被切分"""
        assert _split_pathspec(r"a\/b/c") == [r"a\/b", "c"]

    def test_trailing_escape(self):
        """覆盖分支：以转义符 \ 结尾的孤立情况"""
        assert _split_pathspec(r"a\\") == [r"a\\"]
        assert _split_pathspec(r"a\\\\") == [r"a\\\\"]

    def test_escape_normal_char(self):
        """覆盖分支：转义普通字符"""
        assert _split_pathspec(r"a\b/c") == [r"a\b", "c"]


# ==========================================
# 2. 底层辅助函数 _convert_normal_chunk 测试
# ==========================================
class TestConvertNormalChunk:
    def test_normal_and_wildcards(self):
        """测试普通字符与 glob 通配符"""
        assert _convert_normal_chunk("abc") == "abc"
        assert _convert_normal_chunk("a*b") == "a[^/]*b"
        assert _convert_normal_chunk("a?b") == "a[^/]b"

    def test_charset_standard(self):
        """测试标准字符集及其取反"""
        assert _convert_normal_chunk("[a-z]") == r"(?=[^/])[a-z]"
        assert _convert_normal_chunk("[!a-z]") == r"(?=[^/])[^a-z]"

    def test_charset_ignore_slash(self):
        """覆盖分支：字符集内混入 / 应该被丢弃"""
        assert _convert_normal_chunk("[a/b/c]") == r"(?=[^/])[abc]"

    def test_charset_ignore_escaped_slash(self):
        """覆盖分支：字符集内混入转义的 \/ 同样丢弃 / """
        # 转义状态下遇到 /，执行 pass 分支
        assert _convert_normal_chunk(r"[a\/b]") == r"(?=[^/])[ab]"

    def test_charset_escape_normal_char(self):
        """覆盖分支：字符集内转义普通字符"""
        assert _convert_normal_chunk(r"[\a]") == r"(?=[^/])[\a]"

    def test_empty_charset_fallback(self):
        """覆盖分支：防御性处理空字符集 [] 或 [!] 以防正则编译崩溃"""
        assert _convert_normal_chunk("[]") == "(?!)"
        assert _convert_normal_chunk("[!]") == "(?!)"

    def test_unclosed_charset_fallback(self):
        """覆盖分支：未闭合的字符集平滑回退处理"""
        # [a 会被当成普通字符 \[ 和 a
        assert _convert_normal_chunk("[a") == r"\[a"

    def test_trailing_escape_in_charset(self):
        """覆盖分支：在字符集中以转义符结尾 (未闭合)"""
        assert _convert_normal_chunk(r"[a\\") == r"\[a\\"

    def test_trailing_escape_outside_charset(self):
        """覆盖分支：在普通文本中以转义符结尾"""
        assert _convert_normal_chunk(r"a\\") == r"a\\"

    def test_nested_unclosed_charset(self):
        """覆盖分支：极其变态的未闭合嵌套"""
        # 第一个 [ 未闭合，触发递归回退；第二个 [ 正常闭合
        assert _convert_normal_chunk("[a[b]") == r"\[a(?=[^/])[b]"


# ==========================================
# 3. 核心转换函数 gitattributes_to_regex 测试
# ==========================================
class TestGitattributesToRegex:
    def test_empty_pattern(self):
        """覆盖分支：空规则"""
        assert gitattributes_to_regex("", "") == "(?s)^$"

    def test_root_normalization(self):
        """覆盖分支：root 目录的规范化处理"""
        # root 无需 / 结尾，函数会自动补齐
        assert gitattributes_to_regex("src", "*.py").startswith(r"(?s)^src/")
        # root 带有 / 结尾，不会重复添加
        assert gitattributes_to_regex("src/", "*.py").startswith(r"(?s)^src/")

    def test_explicit_floating(self):
        """覆盖分支：显示浮动前缀 **/" """
        assert gitattributes_to_regex("", "**/foo") == r"(?s)^(?:.*/)?foo(?:$|/.*)"

    def test_explicit_anchor(self):
        """覆盖分支：显示锚定前缀 /"""
        assert gitattributes_to_regex("", "/foo") == r"(?s)^foo(?:$|/.*)"

    def test_implicit_floating_one_chunk(self):
        """覆盖分支：隐式浮动（无内部斜线）"""
        assert gitattributes_to_regex("", "foo") == r"(?s)^(?:.*/)?foo(?:$|/.*)"

    def test_implicit_floating_dir(self):
        """覆盖分支：隐式浮动（只有尾随斜线）"""
        assert gitattributes_to_regex("", "foo/") == r"(?s)^(?:.*/)?foo/.*"

    def test_implicit_anchor(self):
        """覆盖分支：包含内部斜线，转为隐式锚定"""
        assert gitattributes_to_regex("", "a/b") == r"(?s)^a/b(?:$|/.*)"

    def test_filter_consecutive_asterisks(self):
        """覆盖分支：合并连续的 ** """
        # a/**/**/b 应该等效于 a/**/b
        regex1 = gitattributes_to_regex("", "a/**/**/b")
        regex2 = gitattributes_to_regex("", "a/**/b")
        assert regex1 == regex2 == r"(?s)^a/(?:.*/)?b(?:$|/.*)"

    def test_double_asterisk_positions(self):
        """覆盖分支：** 出现的所有位置情况"""
        # 仅有 ** -> .*
        assert gitattributes_to_regex("", "**") == r"(?s)^(?:.*/)?.*"
        # 结尾 ** (非第一个) -> /.*
        assert gitattributes_to_regex("", "a/**") == r"(?s)^a/.*"
        # 开头 ** (随后有内容) -> (?:.*/)?
        assert gitattributes_to_regex("", "**/b") == r"(?s)^(?:.*/)?b(?:$|/.*)"
        # 中间 ** -> /(?:.*/)?
        assert gitattributes_to_regex("", "a/**/b") == r"(?s)^a/(?:.*/)?b(?:$|/.*)"


# ==========================================
# 4. 攻击性/恶意注入测试 (Aggressive / Injection Tests)
# ==========================================
class TestAggressiveRegexInjection:
    """
    专门针对带有恶意、易导致正则语法错误的畸形 gitattributes 进行测试。
    所有生成的表达式必须能被 Python re.compile 成功编译，不引发 re.error！
    """

    def assert_compiles_and_matches(self, pathspec, test_path, should_match=True):
        regex_str = gitattributes_to_regex("", pathspec)
        try:
            compiled = re.compile(regex_str)
        except re.error as e:
            pytest.fail(f"生成的正则无效引发 re.error: {e}\n正则: {regex_str}\n规则: {pathspec}")

        is_match = bool(compiled.match(test_path))
        assert is_match == should_match, f"预期 {'匹配' if should_match else '不匹配'}: 规则 '{pathspec}' vs 路径 '{test_path}'"

    def test_regex_meta_characters(self):
        """攻击：路径中充满正则表达式元字符 . ^ $ + ( ) { } |"""
        malicious_spec = "file(1).^+${txt}"
        # Git会将这些视作普通字符，转换器必须严格 re.escape 这些内容
        self.assert_compiles_and_matches(malicious_spec, "file(1).^+${txt}")
        self.assert_compiles_and_matches(malicious_spec, "fileX1Y.^+${txt}", False)

    def test_malicious_unclosed_brackets_with_meta(self):
        """攻击：未闭合的字符区间内包含正则元字符"""
        # [.*+?^${}()|\] 极易由于 [ 未闭合导致正则编译失败
        malicious_spec = r"[.*+?^${}()|\]"
        self.assert_compiles_and_matches(malicious_spec, r"[.*+?^${}()|\]")

    def test_charset_with_regex_injection(self):
        """攻击：尝试通过字符集闭合漏洞注入正则"""
        # 尝试使用 ]*.* 注入正则的万能匹配
        malicious_spec = "file[a-z]*.*"
        # *.* 会被转换为 [^/]*\.[^/]*，所以不会引发灾难性正则匹配
        self.assert_compiles_and_matches(malicious_spec, "filex.md")
        self.assert_compiles_and_matches(malicious_spec, "fileA.md", False)  # 大写不在 a-z 中

    def test_empty_and_spaces(self):
        """攻击：全是空格和奇奇怪怪的空白符"""
        self.assert_compiles_and_matches("   ", "   ")
        self.assert_compiles_and_matches("a b", "a b")

    def test_extreme_trailing_combinations(self):
        """攻击：极端结尾组合测试"""
        specs = [
            r"foo\\",
            r"foo\\",
            "foo[",
            r"foo\[",
            "foo[!",
            "foo/",
            "foo//",
            "**//**"
        ]
        for spec in specs:
            regex = gitattributes_to_regex("", spec)
            # 只要编译不报错即代表安全拦截成功
            assert re.compile(regex)

    def test_catastrophic_backtracking_prevention(self):
        """验证：防范 ReDoS 灾难性回溯"""
        # 在正则中出现 (.*)* 或连续不设防的 .* 会引发灾难性回溯
        # 我们的算法通过合并连续的 ** 并在 * 处使用 [^/]* 进行了防范
        regex_str = gitattributes_to_regex("", "a/**/**/**/**/b")
        # 验证生成的正则中不存在重复组合
        assert regex_str == r"(?s)^a/(?:.*/)?b(?:$|/.*)"


class TestProjectIntegration:
    """
    复杂前后端项目（FastAPI + SvelteKit）集成测试
    """
    FILES = [
        "README.md",
        ".gitattributes",
        "backend/main.py",
        "backend/api/router.py",
        "backend/models/__init__.py",
        "backend/tests/test_api.py",
        "backend/requirements.txt",
        "backend/.env",
        "frontend/package.json",
        "frontend/vite.config.ts",
        "frontend/svelte.config.js",
        "frontend/src/app.html",
        "frontend/src/routes/+page.svelte",
        "frontend/src/routes/+layout.svelte",
        "frontend/src/routes/api/user/+server.ts",
        "frontend/src/lib/index.ts",
        "frontend/src/lib/components/Button.svelte",
        "frontend/tests/app.spec.ts",
    ]

    def _get_matches(self, root: str, pathspec: str) -> set:
        """运行正则匹配，返回命中的文件集合"""
        regex_str = gitattributes_to_regex(root, pathspec)
        compiled = re.compile(regex_str)
        return {f for f in self.FILES if compiled.match(f)}

    def test_match_all_svelte_files(self):
        matches = self._get_matches("", "*.svelte")
        expected = {
            "frontend/src/routes/+page.svelte",
            "frontend/src/routes/+layout.svelte",
            "frontend/src/lib/components/Button.svelte"
        }
        assert matches == expected

    def test_match_backend_directory(self):
        # 匹配整个 backend 目录下的所有文件
        matches = self._get_matches("", "backend/")
        expected = {f for f in self.FILES if f.startswith("backend/")}
        assert matches == expected
        # 确保没有误伤 frontend 的文件
        assert "frontend/package.json" not in matches

    def test_match_all_python_files(self):
        matches = self._get_matches("", "**/*.py")
        expected = {
            "backend/main.py",
            "backend/api/router.py",
            "backend/models/__init__.py",
            "backend/tests/test_api.py"
        }
        assert matches == expected

    def test_match_specific_root_file(self):
        # 根目录强制锚定匹配
        matches = self._get_matches("", "/README.md")
        expected = {"README.md"}
        assert matches == expected

    def test_match_config_files_with_charset(self):
        # 匹配 vite.config.ts 和 svelte.config.js
        matches = self._get_matches("", "*.*.config.[jt]s")
        # 由于我们文件列表里名字没有额外的一层，实际测试用 *.config.* 更好
        matches = self._get_matches("", "*.config.[jt]s")
        expected = {
            "frontend/vite.config.ts",
            "frontend/svelte.config.js"
        }
        assert matches == expected

    def test_submodule_root(self):
        # 模拟前端文件夹里的 .gitattributes
        matches = self._get_matches("frontend", "src/routes/")
        expected = {
            "frontend/src/routes/+page.svelte",
            "frontend/src/routes/+layout.svelte",
            "frontend/src/routes/api/user/+server.ts",
        }
        assert matches == expected

    def test_double_asterisk_mid_path(self):
        # 匹配 frontend 任意子目录下的 .ts 文件
        matches = self._get_matches("", "frontend/**/*.ts")
        expected = {
            "frontend/vite.config.ts",
            "frontend/src/routes/api/user/+server.ts",
            "frontend/src/lib/index.ts",
            "frontend/tests/app.spec.ts"
        }
        assert matches == expected

    def test_charset_negation(self):
        # 匹配以 + 开头，但后面不包含 p 或 l 的 .svelte 文件
        # (故意避开 +page 和 +layout，不会匹配到任何，或者如果有别的话)
        # 测试：过滤掉 +page，尝试匹配 +layout
        matches = self._get_matches("", "frontend/**/+[!p]*.svelte")
        expected = {
            "frontend/src/routes/+layout.svelte"
        }
        assert matches == expected
