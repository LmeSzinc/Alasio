import pytest

from alasio.ext.path.validate import validate_filename


# We use parametrize to test a wide range of invalid inputs with a single function.
@pytest.mark.parametrize("invalid_name", [
    # Check 1: Type and emptiness
    None,
    123,
    [],
    {},
    "",

    # Check 2: Character length too long
    "a" * 256,

    # Check 3: Illegal characters
    "my/file.txt",
    "my\\file.txt",
    "my:file.txt",
    "my*file.txt",
    'my"file.txt',
    "my?file.txt",
    "my<file.txt",
    "my>file.txt",
    "my|file.txt",

    # Check 3: Control characters
    "file-with-newline\n.txt",
    "file-with-tab\t.txt",
    "file-with-null\0.txt",

    # Check 4: Reserved names
    ".",
    "..",
    "$MFT",
    "con",
    "PRN.txt",
    "lpt1.doc",
    "COM5.zip",
    "NUL",
    "aux.json",

    # Check 5: Invalid start/end characters
    " starts-with-space.txt",
    "ends-with-space.txt ",
    "ends-with-dot.txt.",

    # Check 6: Byte length too long (aggressive test)
    "a" * 253 + "€",  # Char len is 254, but byte len is 256

    # Check 6: Invalid encoding (aggressive test)
    "malformed-\ud800-string.txt",
])
def test_invalid_inputs_raise_value_error(invalid_name):
    """
    Verifies that validate_filename raises a ValueError for any invalid input.
    This test does NOT check the content of the error message, only that an
    exception of the correct type is raised.
    """
    with pytest.raises(ValueError):
        validate_filename(invalid_name)


@pytest.mark.parametrize("valid_name", [
    "file.txt",
    "document-1.docx",
    "image.jpg",
    "a" * 255,  # Max length
])
def test_valid_inputs_do_not_raise_exception(valid_name):
    """
    Verifies that valid filenames do not cause any exception to be raised.
    This is the counterpart to the exception test, ensuring the function
    doesn't fail on good data.
    """
    try:
        validate_filename(valid_name)
    except ValueError:
        pytest.fail(f"validate_filename('{valid_name}') raised an unexpected ValueError.")
