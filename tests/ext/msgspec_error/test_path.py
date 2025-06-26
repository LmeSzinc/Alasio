import pytest

from alasio.ext.msgspec_error.error import get_error_path


class TestGetErrorPathDirectly:
    """
    Directly tests the REWRITTEN get_error_path function with pre-defined strings.
    This version of the test validates that both dot and bracket notations are
    parsed correctly, including complex nesting, path boundaries, and special
    error message formats.
    """

    @pytest.mark.parametrize(
        "error_string, expected_path",
        [
            # === Basic Dot Notation ===
            pytest.param(
                "Expected `int`, got `str` - at `$.user.profile.age`",
                ('user', 'profile', 'age'),
                id="basic_dot_nested"
            ),

            # === Path Start & End Cases ===
            pytest.param(
                "Expected `int`, got `str` - at `$[0]`",
                (0,),
                id="start_list_root"
            ),
            pytest.param(
                "Expected `int`, got `str` - at `$[...].name`",
                ('...', 'name'),
                id="start_dict_nested"
            ),
            pytest.param(
                "Expected `list`, got `int` - at `$.data.items[0]`",
                ('data', 'items', 0),
                id="end_list"
            ),
            pytest.param(
                "Expected `dict`, got `int` - at `$.data.users[...]`",
                ('data', 'users', '...'),
                id="end_dict"
            ),

            # === Complex Nested Combinations ===
            pytest.param(
                "Expected `int`, got `str` - at `$.items[0].details.name`",
                ('items', 0, 'details', 'name'),
                id="combo_dict_in_list"
            ),
            pytest.param(
                "Expected `int`, got `str` - at `$.users[...].roles[0]`",
                ('users', '...', 'roles', 0),
                id="combo_list_in_dict"
            ),
            pytest.param(
                "Expected `int`, got `str` - at `$.matrix[0][1].value`",
                ('matrix', 0, 1, 'value'),
                id="combo_list_in_list"
            ),
            pytest.param(
                # Corrected version for dict-in-dict
                "Expected `int`, got `str` - at `$.users[...][...].name`",
                ('users', '...', '...', 'name'),
                id="combo_dict_in_dict"
            ),
            pytest.param(
                "Expected `int`, got `str` - at `$.data[...][0].value`",
                ('data', '...', 0, 'value'),
                id="combo_dict_of_list"
            ),
            pytest.param(
                "Expected `int`, got `str` - at `$.data[0][...].value`",
                ('data', 0, '...', 'value'),
                id="combo_list_of_dict"
            ),
            pytest.param(
                "Expected `int`, got `str` - at `$.data[0].users[...].permissions[1].action`",
                ('data', 0, 'users', '...', 'permissions', 1, 'action'),
                id="combo_deeply_nested_mix"
            ),

            # === "Object missing" Cases (Root & Sub-paths) ===
            pytest.param(
                "Object missing required field `id`",
                ('id',),
                id="missing_root"
            ),
            pytest.param(
                "Object missing required field `role` - at `$.users[...].profile`",
                ('users', '...', 'profile', 'role'),
                id="missing_subpath_dict"
            ),

            # === "Object unknown" Cases (Root & Sub-paths) ===
            pytest.param(
                "Object contains unknown field `extra_field`",
                ('extra_field',),
                id="unknown_root"
            ),
            pytest.param(
                "Object contains unknown field `color` - at `$.items[1]`",
                ('items', 1, 'color'),
                id="unknown_subpath_list"
            ),

            # === Edge Cases ===
            pytest.param(
                "Expected `int`, got `str`",
                (),
                id="edge_no_path"
            ),
            pytest.param("", (), id="edge_empty_string"),
            pytest.param("A completely unrelated error message", (), id="edge_unrelated_error"),
        ]
    )
    def test_get_error_path_logic(self, error_string, expected_path):
        """Asserts that the parser correctly handles a variety of string formats."""
        assert get_error_path(error_string) == expected_path
