"""
Tests for alasio/backport/literal.py
"""
import sys

import pytest

from alasio.backport.literal import get_literal


class TestGetLiteral:
    """Tests for get_literal function."""

    @pytest.mark.parametrize("values, expected", [
        (('a', 'b', 'c'), ('a', 'b', 'c')),
        ((1, 2, 3), (1, 2, 3)),
        ((True,), (True,)),
        (('only',), ('only',)),
        (('',), ('',)),
    ])
    def test_typing_literal(self, values, expected):
        """get_literal with typing.Literal should return literal values."""
        from typing import Literal
        result = get_literal(Literal.__getitem__(values))
        assert result == expected
        assert isinstance(result, tuple)

    @pytest.mark.parametrize("values, expected", [
        (('x', 'y'), ('x', 'y')),
        ((42,), (42,)),
        ((False,), (False,)),
    ])
    def test_typing_extensions_literal(self, values, expected):
        """get_literal with typing_extensions.Literal should return literal values."""
        from typing_extensions import Literal
        result = get_literal(Literal.__getitem__(values))
        assert result == expected
        assert isinstance(result, tuple)

    @pytest.mark.parametrize("tp", [
        str,
        int,
        float,
        bool,
        list,
        dict,
        type(None),
    ])
    def test_not_literal(self, tp):
        """get_literal with non-Literal types should return None."""
        assert get_literal(tp) is None

    def test_literal_itself(self):
        """get_literal with Literal itself (not subscripted) should return None."""
        from typing import Literal
        assert get_literal(Literal) is None

    @pytest.mark.parametrize("values, expected", [
        (('x', 'y'), ('x', 'y')),
        (('only',), ('only',)),
    ])
    def test_classvar_wrapping_typing_literal(self, values, expected):
        """get_literal with ClassVar[typing.Literal] should unwrap and return literal values."""
        from typing import ClassVar, Literal
        result = get_literal(ClassVar[Literal.__getitem__(values)])
        assert result == expected
        assert isinstance(result, tuple)

    @pytest.mark.parametrize("values, expected", [
        (('x', 'y'), ('x', 'y')),
    ])
    def test_classvar_wrapping_te_literal(self, values, expected):
        """get_literal with ClassVar[typing_extensions.Literal] should unwrap and return."""
        from typing import ClassVar
        from typing_extensions import Literal
        result = get_literal(ClassVar[Literal.__getitem__(values)])
        assert result == expected
        assert isinstance(result, tuple)

    @pytest.mark.parametrize("values, expected", [
        ((42,), (42,)),
        ((0,), (0,)),
    ])
    def test_final_wrapping(self, values, expected):
        """get_literal with Final[Literal] should unwrap and return literal values."""
        from typing import Final, Literal
        result = get_literal(Final[Literal.__getitem__(values)])
        assert result == expected
        assert isinstance(result, tuple)

    def test_annotated_wrapping_typing_extensions(self):
        """get_literal with typing_extensions.Annotated wrapping Literal should unwrap."""
        from typing_extensions import Annotated, Literal
        result = get_literal(Annotated[Literal['hello'], 'meta'])
        assert result == ('hello',)
        assert isinstance(result, tuple)

    @pytest.mark.skipif(sys.version_info < (3, 9), reason="typing.Annotated requires Python 3.9+")
    def test_annotated_wrapping_typing(self):
        """get_literal with typing.Annotated wrapping Literal should unwrap."""
        from typing import Annotated, Literal
        result = get_literal(Annotated[Literal['world'], 'tag'])
        assert result == ('world',)
        assert isinstance(result, tuple)

    def test_annotated_classvar_nesting(self):
        """get_literal with Annotated[ClassVar[Literal]] should unwrap multiple layers."""
        from typing import ClassVar
        from typing_extensions import Annotated, Literal
        result = get_literal(Annotated[ClassVar[Literal['deep']], 'meta'])
        assert result == ('deep',)
        assert isinstance(result, tuple)

    def test_annotated_final_nesting(self):
        """get_literal with Annotated[Final[Literal]] should unwrap multiple layers."""
        from typing import Final
        from typing_extensions import Annotated, Literal
        result = get_literal(Annotated[Final[Literal[99]], 'meta', 'extra'])
        assert result == (99,)
        assert isinstance(result, tuple)

    def test_multiple_metadata(self):
        """get_literal with Annotated having multiple metadata args works."""
        from typing_extensions import Annotated, Literal
        result = get_literal(Annotated[Literal['a'], 'x', 'y', 'z'])
        assert result == ('a',)
        assert isinstance(result, tuple)

    def test_annotated_without_literal(self):
        """get_literal with Annotated wrapping non-Literal should return None."""
        from typing_extensions import Annotated
        result = get_literal(Annotated[str, 'meta'])
        assert result is None

    def test_classvar_without_literal(self):
        """get_literal with ClassVar wrapping non-Literal should return None."""
        from typing import ClassVar
        result = get_literal(ClassVar[int])
        assert result is None

    def test_final_without_literal(self):
        """get_literal with Final wrapping non-Literal should return None."""
        from typing import Final
        result = get_literal(Final[int])
        assert result is None
