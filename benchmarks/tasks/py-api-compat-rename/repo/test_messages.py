from messages import build_message


def test_build_message_uses_default_prefix():
    assert build_message("Ada") == "Hello, Ada!"


def test_build_message_accepts_custom_prefix():
    assert build_message("Grace", "Welcome") == "Welcome, Grace!"
