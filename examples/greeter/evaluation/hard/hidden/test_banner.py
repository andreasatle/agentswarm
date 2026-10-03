import pytest
from greeter import banner


def test_banner():
    assert banner("Ada", 15) == "***************\n* Hello, Ada! *\n***************"
    assert banner("Ada", 16).splitlines()[1] == "* Hello, Ada!  *"


def test_too_narrow():
    with pytest.raises(ValueError):
        banner("Ada", 14)
