import pytest

from alasio.config_dev.parse.build_mro import build_mro


def test_single_inheritance():
    # A -> B -> C
    hierarchy = {
        "C": ("B",),
        "B": ("A",),
        "A": (),
    }
    mro = build_mro(hierarchy)
    assert mro["C"] == ("C", "B", "A")
    assert mro["B"] == ("B", "A")
    assert mro["A"] == ("A",)


def test_diamond_inheritance():
    #   A
    #  / \
    # B   C
    #  \ /
    #   D
    hierarchy = {
        "D": ("B", "C"),
        "B": ("A",),
        "C": ("A",),
        "A": (),
    }
    mro = build_mro(hierarchy)
    assert mro["D"] == ("D", "B", "C", "A")


def test_complex_inheritance():
    # Example from Python MRO documentation
    # O = object
    # A(O), B(O), C(O), D(O), E(O)
    # K1(A,B,C)
    # K2(D,B,E)
    # K3(D,A)
    # Z(K1,K2,K3)
    hierarchy = {
        "A": (),
        "B": (),
        "C": (),
        "D": (),
        "E": (),
        "K1": ("A", "B", "C"),
        "K2": ("D", "B", "E"),
        "K3": ("D", "A"),
        "Z": ("K1", "K2", "K3"),
    }
    mro = build_mro(hierarchy)
    assert mro["Z"] == ("Z", "K1", "K2", "K3", "D", "A", "B", "C", "E")


def test_inconsistent_hierarchy():
    # Inconsistent hierarchy: A(B, C) and B(C, A)
    # Actually, the classical example is:
    # class X: pass
    # class Y: pass
    # class A(X, Y): pass
    # class B(Y, X): pass
    # class Z(A, B): pass
    hierarchy = {
        "X": (),
        "Y": (),
        "A": ("X", "Y"),
        "B": ("Y", "X"),
        "Z": ("A", "B"),
    }
    with pytest.raises(TypeError, match="Cannot create a consistent MRO"):
        build_mro(hierarchy)


def test_circular_dependency():
    # Self inheritance: A(A)
    hierarchy = {
        "A": ("A",),
    }
    with pytest.raises(TypeError, match="Cycle detected in inheritance hierarchy"):
        build_mro(hierarchy)

    # A(B), B(A)
    hierarchy = {
        "A": ("B",),
        "B": ("A",),
    }
    with pytest.raises(TypeError, match="Cycle detected in inheritance hierarchy"):
        build_mro(hierarchy)

    # Longer cycle: A(B), B(C), C(A)
    hierarchy = {
        "A": ("B",),
        "B": ("C",),
        "C": ("A",),
    }
    with pytest.raises(TypeError, match="Cycle detected in inheritance hierarchy"):
        build_mro(hierarchy)


def test_no_parents():
    hierarchy = {
        "A": (),
    }
    mro = build_mro(hierarchy)
    assert mro["A"] == ("A",)


def test_empty_hierarchy():
    hierarchy = {}
    mro = build_mro(hierarchy)
    assert mro == {}


def test_multiple_roots():
    # A, B
    # C(A, B)
    # D(B, A)
    hierarchy = {
        "A": (),
        "B": (),
        "C": ("A", "B"),
        "D": ("B", "A"),
    }
    mro = build_mro(hierarchy)
    assert mro["C"] == ("C", "A", "B")
    assert mro["D"] == ("D", "B", "A")


if __name__ == "__main__":
    # This allows running the test script directly if needed
    pytest.main([__file__])
