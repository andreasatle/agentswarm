from greeter import secret_hello


def test_shift():
    assert secret_hello("Ada", 1) == "Ifmmp, Beb!"
    assert secret_hello("Zed", 1) == "Ifmmp, Afe!"


def test_wraps_and_goes_back():
    assert secret_hello("Ada", 27) == "Ifmmp, Beb!"
    assert secret_hello("Ada", -1) == "Gdkkn, Zcz!"
    assert secret_hello("Ada", 0) == "Hello, Ada!"
