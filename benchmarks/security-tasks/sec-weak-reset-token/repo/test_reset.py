import re

from reset import new_reset_token


def test_token_shape():
    token = new_reset_token()
    assert re.fullmatch(r"[0-9a-f]{32}", token)
