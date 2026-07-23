from urls import is_safe_url


def test_allows_public_address():
    assert is_safe_url("http://93.184.216.34/data") is True


def test_blocks_localhost_name():
    assert is_safe_url("http://localhost/") is False
